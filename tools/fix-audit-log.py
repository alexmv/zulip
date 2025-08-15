#!/usr/bin/env python3

# https://chat.zulip.org/#narrow/channel/191-kandra-support/topic/Internal.20server.20error.20when.20trying.20to.20access.20Zulip/near/2244152

import argparse
import os
import sys

ZULIP_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ZULIP_PATH not in sys.path:
    sys.path.append(ZULIP_PATH)
from scripts.lib.setup_path import setup_path

setup_path()

os.environ["DJANGO_SETTINGS_MODULE"] = "zproject.settings"
import django

django.setup()

from zerver.models import RealmAuditLog, Recipient, Stream, Subscription, UserMessage, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType


def fix_user_history(user_id: int, *, dry_run: bool = False) -> None:
    user = UserProfile.objects.get(id=user_id)
    subscriptions = Subscription.objects.filter(
        user_profile_id=user_id, recipient__type=Recipient.STREAM
    )
    for sub in subscriptions:
        stream = Stream.objects.get(recipient_id=sub.recipient_id)
        last_event = (
            RealmAuditLog.objects.filter(
                realm_id=user.realm_id,
                modified_stream_id=stream.id,
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

        print(f"Inconsistency with #{stream.name}")
        # We have some inconsistency.  If there's not public-history,
        # warn and move on -- this takes care and has no right answer.
        if not stream.history_public_to_subscribers:
            print("  No public history -- don't know what to do!")
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
                modified_stream=stream,
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
                modified_stream=stream,
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
                modified_stream=stream,
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--wet-run", action="store_true", default=False)
    options = parser.parse_args()
    fix_user_history(options.user_id, dry_run=not options.wet_run)
