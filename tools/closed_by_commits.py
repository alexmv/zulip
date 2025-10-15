#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache

import requests
from github import Auth, Github
from typing_extensions import override

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def encode_hash_component(s: str) -> str:
    hash_replacements = {
        "%": ".",
        "(": ".28",
        ")": ".29",
        ".": ".2E",
    }
    encoded = urllib.parse.quote(s, safe="*")
    return "".join(hash_replacements.get(c, c) for c in encoded)


@cache
def search_czo_for_number(number: int) -> frozenset[str]:
    params = {
        "anchor": "newest",
        "num_before": "100",
        "num_after": "0",
        "narrow": json.dumps(
            [
                {"negated": False, "operator": "search", "operand": f'"#{number}"'},
                {"negated": False, "operator": "channels", "operand": "web-public"},
            ]
        ),
    }

    try:
        response = requests.get("https://chat.zulip.org/json/messages", params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        messages = data.get("messages", [])

        # Extract unique topic URLs
        urls = set()
        for msg in messages:
            stream_id = msg.get("stream_id")
            display_recipient = msg.get("display_recipient")
            subject = msg.get("subject")

            if not stream_id or not display_recipient or not subject:
                continue

            encoded_recipient = encode_hash_component(display_recipient.replace(" ", "-"))
            encoded_subject = encode_hash_component(subject)
            url = f"https://chat.zulip.org/#narrow/channel/{stream_id}-{encoded_recipient}/topic/{encoded_subject}"
            urls.add(url)

        return frozenset(urls)

    except requests.exceptions.RequestException as e:
        assert e.response
        if e.response.status_code != 429:
            raise
        retry_after = int(e.response.headers["Retry-After"]) + 1
        logger.info("Rate-limited; sleeping for %d seconds", retry_after)
        time.sleep(retry_after)
        return search_czo_for_number(number)


@dataclass
class Issue:
    number: int
    title: str
    czo_urls: set[str] = field(default_factory=set)
    closed_by_prs: list["PullRequest"] = field(default_factory=list)

    @override
    def __hash__(self) -> int:
        return hash(self.number)

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Issue) and self.number == other.number


@dataclass
class PullRequest:
    number: int
    title: str
    czo_urls: set[str] = field(default_factory=set)

    @override
    def __hash__(self) -> int:
        return hash(self.number)

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, PullRequest) and self.number == other.number


class CommitRangeAnalyzer:
    COMMIT_PRS_QUERY = """
    query($oid: GitObjectID!) {
      repository(owner: "zulip", name: "zulip") {
        object(oid: $oid) {
          ... on Commit {
            messageBody
            associatedPullRequests(first: 10) {
              nodes {
                number
                title
                url
                body
                comments(first: 100) {
                  nodes {
                    body
                  }
                }
                closingIssuesReferences(first: 50) {
                  nodes {
                    number
                    title
                    url
                    body
                    comments(first: 100) {
                      nodes {
                        body
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    ISSUE_QUERY = """
    query($number: Int!) {
      repository(owner: "zulip", name: "zulip") {
        issue(number: $number) {
          number
          title
          body
          comments(first: 100) {
            nodes {
              body
            }
          }
        }
      }
    }
    """

    def __init__(self, token: str) -> None:
        self.github = Github(auth=Auth.Token(token))

    @staticmethod
    def _extract_czo_urls(text: str | None) -> set[str]:
        if not text:
            return set()

        matches = re.findall(r"https://chat\.zulip\.org/[^\s\)\]\>]+", text)

        urls = set()
        for url in matches:
            if "/topic/" not in url:
                continue
            urls.add(re.sub(r"/(with|near)/\d+$", "", url))

        return urls

    @staticmethod
    def _extract_issue_numbers(text: str | None) -> set[int]:
        if not text:
            return set()

        # https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword
        pattern = r"^(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)(?:\s+|:\s*)#(\d+)"
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        return {int(num) for num in matches}

    def _fetch_issue(self, number: int) -> Issue | None:
        """Fetch issue metadata from GitHub."""
        try:
            _, result = self.github.requester.graphql_query(self.ISSUE_QUERY, {"number": number})

            if "errors" in result:
                logger.warning("Failed to fetch issue #%d", number)
                return None

            data = result.get("data", {})
            issue_node = data.get("repository", {}).get("issue")

            if not issue_node:
                return None

            issue = Issue(
                number=issue_node["number"],
                title=issue_node["title"].strip(),
            )
            issue_comments = issue_node.get("comments", {}).get("nodes", [])
            for node in [issue_node, *issue_comments]:
                issue.czo_urls.update(self._extract_czo_urls(node.get("body")))
            issue.czo_urls.update(search_czo_for_number(issue.number))

            return issue

        except Exception as e:
            logger.warning("Error fetching issue #%d: %s", number, e)
            return None

    def get_issues_for_commit(self, commit_sha: str) -> dict[Issue, set[PullRequest]]:
        _, result = self.github.requester.graphql_query(self.COMMIT_PRS_QUERY, {"oid": commit_sha})

        if "errors" in result:
            error_messages = [e.get("message", str(e)) for e in result["errors"]]
            raise RuntimeError(f"GraphQL errors: {', '.join(error_messages)}")

        data = result.get("data", {})

        if not data.get("repository", {}).get("object"):
            return dict()

        commit_obj = data["repository"]["object"]
        commit_message = commit_obj.get("messageBody", "")
        pr_nodes = commit_obj.get("associatedPullRequests", {}).get("nodes", [])

        results: dict[Issue, set[PullRequest]] = defaultdict(set)
        for pr_node in pr_nodes:
            pr = PullRequest(
                number=pr_node["number"],
                title=pr_node["title"].strip(),
            )
            pr_comments = pr_node.get("comments", {}).get("nodes", [])
            for node in [pr_node, *pr_comments]:
                pr.czo_urls.update(self._extract_czo_urls(node.get("body")))
            pr.czo_urls.update(search_czo_for_number(pr.number))

            # Get issues from PR metadata
            issues_dict = {}
            issue_nodes = pr_node.get("closingIssuesReferences", {}).get("nodes", [])
            for issue_node in issue_nodes:
                if issue_node is None:
                    continue

                issue = Issue(
                    number=issue_node["number"],
                    title=issue_node["title"].strip(),
                )
                issue_comments = issue_node.get("comments", {}).get("nodes", [])
                for node in [issue_node, *issue_comments]:
                    issue.czo_urls.update(self._extract_czo_urls(node.get("body")))
                issue.czo_urls.update(search_czo_for_number(issue.number))
                issues_dict[issue.number] = issue

            # Extract additional issue numbers from commit message
            for issue_num in self._extract_issue_numbers(commit_message):
                if issue_num not in issues_dict:
                    maybe_issue = self._fetch_issue(issue_num)
                    if maybe_issue is None:
                        continue
                    issues_dict[issue_num] = maybe_issue

            for issue in issues_dict.values():
                results[issue].add(pr)

        if not pr_nodes:
            for issue_num in self._extract_issue_numbers(commit_message):
                maybe_issue = self._fetch_issue(issue_num)
                if maybe_issue is None:
                    continue
                results[maybe_issue].update()

        return results

    def analyze_range(self, base: str, head: str) -> list[Issue]:
        repository = self.github.get_repo("zulip/zulip")
        comparison = repository.compare(base, head)
        commit_shas = [commit.sha for commit in comparison.commits]
        logger.info("Found %d commits", len(commit_shas))

        issue_to_prs: dict[Issue, set[PullRequest]] = defaultdict(set)
        for i, sha in enumerate(commit_shas, 1):
            logger.debug("Processing commit %d/%d: %s", i, len(commit_shas), sha)

            for issue, prs in self.get_issues_for_commit(sha).items():
                issue_to_prs[issue].update(prs)

        final_issues = []
        for issue, prs in issue_to_prs.items():
            for pr in prs:
                issue.czo_urls.update(pr.czo_urls)

            issue.closed_by_prs = sorted(prs, key=lambda p: p.number)
            final_issues.append(issue)

        logger.info(
            "Found %d unique PRs closing %d issues",
            len({pr for prs in issue_to_prs.values() for pr in prs}),
            len(final_issues),
        )

        return sorted(final_issues, key=lambda x: x.number)


def main(options: argparse.Namespace) -> int:
    token = options.token or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GitHub token required; set GITHUB_TOKEN environment variable")

    lines = []
    for issue in CommitRangeAnalyzer(token).analyze_range(options.base, options.head):
        if not issue.closed_by_prs and not issue.czo_urls:
            # Closed an issue that was never discussed anywhere?
            continue

        lines.append(f"## #{issue.number}: {issue.title}")
        lines.extend(f" - **Closed by:** #{pr.number}: {pr.title}" for pr in issue.closed_by_prs)
        lines.extend(f" - {czo_url}" for czo_url in sorted(issue.czo_urls))

        lines.append("")

    print("\n".join(lines))

    return 0


if __name__ == "__main__":
    description = "Find issues which are closed in a commit range"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("base", help="Base commit (refname or commit SHA)")
    parser.add_argument("head", help="Head commit (refname or commit SHA)")
    parser.add_argument("--token", help="GitHub token (or set GITHUB_TOKEN in environment)")

    sys.exit(main(parser.parse_args()))
