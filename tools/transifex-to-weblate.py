#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "typer",
#   "wlc",
#   "requests_cache",
#   "rich",
#   "transifex-python",
# ]
# [tool.uv]
# exclude-newer = "2025-05-30T00:00:00Z"
# ///

import csv
import time
from pathlib import Path
from typing import Annotated

import typer  # type: ignore[import-not-found]
import wlc  # type: ignore[import-not-found]
from requests import HTTPError
from requests_cache import DO_NOT_CACHE, install_cache  # type: ignore[import-not-found]
from requests_cache.backends.sqlite import SQLiteDict  # type: ignore[import-not-found]
from rich.console import Console
from rich.progress import Progress, track
from transifex.api import transifex_api  # type: ignore[import-not-found]


class WeblateSearchError(Exception):
    pass


def setup(transifex_key: str, weblate_key: str) -> wlc.Weblate:
    install_cache(
        allowable_methods=("GET",),
        allowable_codes=(200,),
        urls_expire_after={
            "rest.api.transifex.com/resource_translations": DO_NOT_CACHE,
            "hosted.weblate.org/api/translations/zulip/*/*/units/": DO_NOT_CACHE,
            "hosted.weblate.org/api/units/": DO_NOT_CACHE,
        },
    )

    transifex_api.setup(auth=transifex_key)

    w = wlc.Weblate(
        key=weblate_key,
        url="https://hosted.weblate.org/api/",
        retries=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
    )

    # Works around a bug in wlc
    w.adapter.max_retries = w.retries

    return w


unit_cache = SQLiteDict("http_cache", table_name="unit_cache")


def unit_cache_get(component: str, language: str, source_string: str) -> int | None:
    cache_key = f"{component}/{language}/{source_string}"
    return unit_cache.get(cache_key)


def unit_cache_set(component: str, language: str, source_string: str, id: int) -> None:
    cache_key = f"{component}/{language}/{source_string}"
    unit_cache[cache_key] = id


def find_unit(w: wlc.Weblate, component: str, language: str, source_string: str) -> wlc.Unit:
    unit_id: int | None = unit_cache_get(component, language, source_string)
    if unit_id:
        return w.get_unit(str(unit_id))

    if "\n" in source_string:
        terms: list[str] = []
        for line in source_string.splitlines():
            trimmed = line.strip()
            if not trimmed:
                continue
            if '"' not in trimmed:
                terms.append(f'source:"{trimmed}"')
            elif "'" not in trimmed:
                terms.append(f"source:'{trimmed}'")
            else:
                terms.append("source:'" + trimmed.replace("'", " ") + "'")
    elif '"' not in source_string:
        terms = [f'source:="{source_string}"']
    elif "'" not in source_string:
        terms = [f"source:='{source_string}'"]
    else:
        terms = ["source:'" + source_string.replace("'", " ") + "'"]
    found = list(
        w.list_units(
            f"translations/zulip/{component}/{language}/units/",
            params={"q": " ".join(terms)},
        )
    )
    if len(found) != 1:
        raise WeblateSearchError(
            f"{component} / {language} / {source_string} = {terms} / {len(found)}"
        )

    unit_cache_set(component, language, source_string, found[0].id)

    return found[0]


def upload_glossary(
    w: wlc.Weblate, languages: list[transifex_api.Language], glossary_csv: Path
) -> None:
    with open(glossary_csv) as csvfile:
        reader = csv.DictReader(csvfile)
        glossary_rows = list(reader)
        for row in track(glossary_rows):
            for language in languages:
                if not row[f"translation_{language.code.lower()}"]:
                    continue
                weblate_lang = language.code.replace("zh-", "zh_")
                if weblate_lang == "zh_Hant":
                    continue
                elif weblate_lang == "zh_TW":
                    weblate_lang = "zh_Hant"
                csv_lang = language.code.lower()

                unit_id: int | None = unit_cache_get("glossary", weblate_lang, row["term"])
                if unit_id is not None:
                    # Already exists, and we know its ID.
                    w.request(
                        "PATCH",
                        f"units/{unit_id}/",
                        data={
                            "state": 20,
                            "target": [row[f"translation_{csv_lang}"]],
                        },
                    )
                    # We skip commenting because we already inserted
                    # the comment when we did the first go-around, and
                    # checking that it's there would be another
                    # request.
                    continue

                # May or may not already exist; all we know is it's
                # not in our cache
                try:
                    # Attempt to create it, since that's the most
                    # likely change it needs
                    while True:
                        # This request is a POST that we want to retry
                        # if it 503's, since at worst we already
                        # created it, and we'll end up in the 400
                        # codepath below.
                        try:
                            resp = w.request(
                                "POST",
                                f"translations/zulip/glossary/{weblate_lang}/units/",
                                data={
                                    "source": [row["term"]],
                                    "target": [row[f"translation_{csv_lang}"]],
                                },
                            )
                            break
                        except wlc.WeblateException as e:
                            assert isinstance(e.__cause__, HTTPError)
                            if e.__cause__.response.status_code != 503:
                                raise
                            time.sleep(1)

                    unit_id = resp["id"]
                    # And insert into the cache for next time
                    unit_cache_set("glossary", weblate_lang, row["term"], unit_id)
                except wlc.WeblateException as e:
                    # We hit an non-503 error when making it --
                    # probably because it already exists
                    assert isinstance(e.__cause__, HTTPError)
                    if e.__cause__.response.status_code != 400:
                        raise

                    # Go find it and change it, as the above codepath.
                    unit = find_unit(w, "glossary", weblate_lang, row["term"])
                    unit_id = unit.id
                    resp = w.request(
                        "PATCH",
                        f"units/{unit.id}/",
                        data={
                            "state": 20,
                            "target": [row[f"translation_{csv_lang}"]],
                        },
                    )
                    if unit.has_comment:
                        continue

                if row[f"notes_{csv_lang}"]:
                    # The API does not provide a way to update the
                    # "notes" field, so we instead add a comment.
                    w.request(
                        "POST",
                        f"units/{unit_id}/comments/",
                        data={
                            "scope": "translation",
                            "comment": "Original note from Transifex:\n\n"
                            + row[f"notes_{csv_lang}"],
                        },
                    )


def update_reviewed(
    w: wlc.Weblate,
    weblate_component: str,
    languages: list[transifex_api.Language],
    resource: transifex_api.Resource,
) -> None:
    for language in languages:
        with Progress() as progress:
            task = progress.add_task(f"{language.code:8}", start=False)
            reviewed_translations = transifex_api.ResourceTranslation.filter(
                resource=resource,
                language=language,
                reviewed="true",
            ).include("resource_string")
            proofread_translations = transifex_api.ResourceTranslation.filter(
                resource=resource,
                language=language,
                proofread="true",
            ).include("resource_string")
            all_tasks = [*reviewed_translations, *proofread_translations]
            progress.start_task(task)
            for translation in progress.track(all_tasks, task_id=task):
                try:
                    unit = find_unit(
                        w,
                        weblate_component,
                        language.code,
                        translation.resource_string.strings["other"],
                    )
                except WeblateSearchError as e:
                    progress.print(e)
                    continue
                if unit.approved:
                    continue
                unit.patch(state=30, target=unit.target)


def update_comments(
    w: wlc.Weblate,
    weblate_component: str,
    organization: transifex_api.Organization,
    resource: transifex_api.Resource,
) -> None:
    has_comments = transifex_api.ResourceStringComment.filter(
        organization=organization,
        resource=resource,
        status="open",
    )
    for comment in track(has_comments, description="Comments"):
        comment.fetch("language", "resource_string", "author")
        unit = find_unit(
            w,
            weblate_component,
            comment.language.code,
            comment.resource_string.strings["other"],
        )

        if unit.has_comment:
            continue

        user_link = f"https://app.transifex.com/user/profile/{comment.author.username}/"
        w.request(
            "POST",
            f"units/{unit.id}/comments/",
            data={
                "scope": "translation",
                "comment": comment.message
                + f"\n\n(Originally from [`{comment.author.username}` on Transifex]({user_link}))",
                "timestamp": comment.datetime_created,
            },
        )


def main(
    transifex_key: Annotated[str, typer.Option(envvar="TRANSIFEX_KEY")],
    weblate_key: Annotated[str, typer.Option(envvar="WEBLATE_KEY")],
    comments: bool = True,
    reviews: bool = True,
    only_language: list[str] | None = None,
    glossary_csv: Path | None = None,
) -> None:
    w = setup(transifex_key, weblate_key)

    t_org = transifex_api.Organization.get(slug="Zulip")
    t_project = t_org.fetch("projects").get(slug="zulip")
    languages = t_project.fetch("languages")
    if only_language:
        languages = [lang for lang in languages if lang.code in only_language]

    resource_map = {
        "djangopo": ["django", "django-9-x", "django-10-x"],
        "translationsjson": ["frontend", "frontend-9-x", "frontend-10-x"],
        "desktopjson": ["desktop"],
    }

    console = Console()

    if glossary_csv:
        console.print("[bold]Glossary[/bold]")
        try:
            w.request("POST", "components/zulip/glossary/lock/", data={"lock": False})
            upload_glossary(w, languages, glossary_csv)
        finally:
            w.request("POST", "components/zulip/glossary/lock/", data={"lock": True})

    if reviews or comments:
        for transifex_resource, weblate_components in resource_map.items():
            weblate_component = weblate_components[0]
            propagate_components = weblate_components[1:]
            console.print(f"[bold]{weblate_component}[/bold]")
            try:
                w.request(
                    "POST", f"components/zulip/{weblate_component}/lock/", data={"lock": False}
                )
                for component in propagate_components:
                    w.request("POST", f"components/zulip/{component}/lock/", data={"lock": False})

                # There's no way to do an exact search?
                resources = t_project.fetch("resources").filter(slug=transifex_resource)
                resource = next(r for r in resources if r.slug == transifex_resource)

                if reviews:
                    update_reviewed(w, weblate_component, languages, resource)

                if comments:
                    update_comments(w, weblate_component, t_org, resource)
            finally:
                w.request(
                    "POST", f"components/zulip/{weblate_component}/lock/", data={"lock": True}
                )
                for component in propagate_components:
                    w.request("POST", f"components/zulip/{component}/lock/", data={"lock": True})


if __name__ == "__main__":
    typer.run(main)
