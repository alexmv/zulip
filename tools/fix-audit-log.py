#!/usr/bin/env python3

# https://chat.zulip.org/#narrow/channel/191-kandra-support/topic/Internal.20server.20error.20when.20trying.20to.20access.20Zulip/near/2244152

import argparse
import os
import sys
from collections import defaultdict

ZULIP_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ZULIP_PATH not in sys.path:
    sys.path.append(ZULIP_PATH)
from scripts.lib.setup_path import setup_path

setup_path()

os.environ["DJANGO_SETTINGS_MODULE"] = "zproject.settings"
import django
from django.db.models import Exists, OuterRef

django.setup()

import sentry_sdk

# This deactivates Sentry
sentry_sdk.init()

from zerver.models import Message, RealmAuditLog, Subscription, UserMessage, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType


def fix_user_history(user: UserProfile, *, dry_run: bool = True, detect: bool = False) -> bool:
    subscriptions = Subscription.objects.filter(
        user_profile_id=user.id,
    ).extra(  # noqa: S610
        tables=("zerver_stream",),
        where=(
            "zerver_stream.recipient_id = zerver_subscription.recipient_id",
            "not zerver_stream.deactivated",
        ),
        select={
            "stream_id": "zerver_stream.id",
            "stream_name": "zerver_stream.name",
            "history_public_to_subscribers": "zerver_stream.history_public_to_subscribers",
        },
    )
    inconsistent = False
    for sub in subscriptions.iterator():
        last_event = (
            RealmAuditLog.objects.filter(
                realm_id=user.realm_id,
                modified_stream_id=sub.stream_id,
                modified_user_id=user.id,
                event_type__in=(301, 302, 303),
            )
            .order_by("-id")
            .first()
        )
        if last_event is not None:
            if (
                last_event.event_type
                in (
                    AuditLogEventType.SUBSCRIPTION_ACTIVATED,
                    AuditLogEventType.SUBSCRIPTION_CREATED,
                )
                and sub.active
            ):
                continue
            if (
                last_event.event_type == AuditLogEventType.SUBSCRIPTION_DEACTIVATED
                and not sub.active
            ):
                continue

        if detect:
            return True
        inconsistent = True
        print(
            f"Inconsistency with #{sub.stream_name} / id = {sub.stream_id} / recipient = {sub.recipient_id}"
        )
        # We have some inconsistency.  If there's not public-history,
        # warn and move on -- this takes care and has no right answer.
        if not sub.history_public_to_subscribers:
            # Check if they have a UserMessage for all messages
            recipient_id = sub.recipient_id
            assert recipient_id is not None
            messages = Message.objects.filter(recipient_id=recipient_id)
            messages_without_ums = messages.alias(
                has_usermessage=Exists(
                    UserMessage.objects.filter(user_profile_id=user.id, message_id=OuterRef("id"))
                )
            ).filter(has_usermessage=False)
            if messages_without_ums.exists():
                # There are messages in the stream that the user didn't get.  We don't know how to handle this generally.
                print(
                    "  Private stream without shared history, and the user didn't get all of them!"
                )
                continue

        # If there's no events, we make a SUBSCRIPTION_CREATED at the
        # same time as the SUBSCRIPTION_CREATED for the Subscription
        # row right before them.
        if last_event is None:
            prior_auditlog = (
                RealmAuditLog.objects.filter(event_type=AuditLogEventType.SUBSCRIPTION_CREATED)
                .extra(  # noqa: S610
                    tables=("zerver_stream", "zerver_subscription"),
                    where=(
                        "zerver_realmauditlog.modified_stream_id = zerver_stream.id",
                        "zerver_realmauditlog.modified_user_id = zerver_subscription.user_profile_id",
                        "zerver_subscription.recipient_id = zerver_stream.recipient_id",
                        "zerver_subscription.id < %s",
                    ),
                    params=(sub.id,),
                    order_by=["-zerver_subscription.id"],
                )
                .first()
            )
            if not prior_auditlog:
                print("!! No prior audit log??")
                continue
            create_event = RealmAuditLog(
                realm_id=user.realm_id,
                acting_user=None,
                modified_user=user,
                modified_stream_id=sub.stream_id,
                event_last_message_id=prior_auditlog.event_last_message_id,
                event_type=AuditLogEventType.SUBSCRIPTION_CREATED,
                event_time=prior_auditlog.event_time,
                backfilled=True,
            )
            if dry_run:
                print(f"  Would insert: {create_event}")
            else:
                print(f"  Inserting: {create_event}")
                create_event.save()
            last_event = create_event
        elif sub.active:
            first_usermessage = (
                UserMessage.objects.filter(
                    user_profile_id=user.id,
                    message__recipient_id=sub.recipient_id,
                    message__date_sent__gt=last_event.event_time,
                )
                .order_by("id")
                .first()
            )
            if first_usermessage:
                event_time = first_usermessage.message.date_sent
                event_last_message_id = first_usermessage.message.id - 1
            else:
                print("  !!! No UserMessage after deactivation!")
                continue

            activate_event = RealmAuditLog(
                realm_id=user.realm_id,
                acting_user=None,
                modified_user=user,
                modified_stream_id=sub.stream_id,
                event_last_message_id=event_last_message_id,
                event_type=AuditLogEventType.SUBSCRIPTION_ACTIVATED,
                event_time=event_time,
                backfilled=True,
            )
            if dry_run:
                print(f"  Would insert: {activate_event}")
            else:
                print(f"  Inserting: {activate_event}")
                activate_event.save()
        if not sub.active:
            last_usermessage = (
                UserMessage.objects.filter(
                    user_profile_id=user.id, message__recipient_id=sub.recipient_id
                )
                .order_by("-id")
                .first()
            )
            if last_usermessage:
                event_time = last_usermessage.message.date_sent
                event_last_message_id = last_usermessage.message.id
            else:
                event_time = last_event.event_time
                assert last_event.event_last_message_id is not None
                event_last_message_id = last_event.event_last_message_id

            deactivate_event = RealmAuditLog(
                realm_id=user.realm_id,
                acting_user=None,
                modified_user=user,
                modified_stream_id=sub.stream_id,
                event_last_message_id=event_last_message_id,
                event_type=AuditLogEventType.SUBSCRIPTION_DEACTIVATED,
                event_time=event_time,
                backfilled=True,
            )
            if dry_run:
                print(f"  Would insert: {deactivate_event}")
            else:
                print(f"  Inserting: {deactivate_event}")
                deactivate_event.save()
    return inconsistent


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id")
    target.add_argument("--realm-id")
    target.add_argument("--all-users-in-orgs-with-deleted-users", action="store_true")

    parser.add_argument("--wet-run", action="store_true", default=False)
    options = parser.parse_args()

    if options.user_id:
        user = UserProfile.objects.get(id=options.user_id)
        fix_user_history(user, dry_run=not options.wet_run)
    elif options.realm_id:
        for user in UserProfile.objects.filter(
            realm_id=options.realm_id, is_active=True, is_bot=False
        ).iterator():
            if options.wet_run:
                fix_user_history(user, dry_run=False)
            elif fix_user_history(user, detect=True):
                print(f"{user.id} / {user.delivery_email}")
    else:
        distinct_realm_ids = (
            RealmAuditLog.objects.filter(
                event_type__in=(
                    AuditLogEventType.USER_DELETED,
                    AuditLogEventType.USER_DELETED_PRESERVING_MESSAGES,
                ),
            )
            .values_list("realm_id", flat=True)
            .distinct()
        )

        potential_users = (
            UserProfile.objects.filter(
                is_active=True,
                is_bot=False,
            )
            .exclude(
                realm_id__in=distinct_realm_ids,
            )
            .select_related("realm")
            .only("id", "delivery_email", "realm_id", "realm__string_id")
            .order_by("-realm_id", "id")
        )
        affected_realms: dict[str, int] = defaultdict(int)
        for user in potential_users.iterator():
            if fix_user_history(user, detect=True):
                print(f"{user.realm_id} {user.realm.string_id} / {user.id} / {user.delivery_email}")
                # print(".", end="", flush=True)
                affected_realms[user.realm.string_id] += 1
        print()
        for string_id, v in sorted(affected_realms.items(), key=lambda e: -e[1]):
            print(f"{string_id}: {v}")
