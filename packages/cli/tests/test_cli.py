from __future__ import annotations

from io import StringIO
import re
import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from agentreview.cli import main
from agentreview.git.diff import get_diff
from agentreview.git.metadata import get_metadata
from agentreview.payload.encode import encode_payload, write_payload
from agentreview.payload.types import AgentReviewFile, AgentReviewPayload, PayloadMeta
from agentreview.vcs import Repository


def _completed(stdout: str, *, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args or ["git"], returncode=0, stdout=stdout, stderr="")


def _failed(
    stderr: str,
    *,
    args: list[str] | None = None,
    returncode: int = 255,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args or ["sl"],
        returncode=returncode,
        stdout="",
        stderr=stderr,
    )


class GetDiffTests(unittest.TestCase):
    @patch(
        "agentreview.git.diff._get_untracked_files_diff",
        return_value="diff --git a/new.txt b/new.txt",
    )
    @patch("agentreview.git.diff._run_git")
    def test_branch_mode_includes_uncommitted_and_untracked_changes(self, run_git, get_untracked) -> None:
        repo = Repository(kind="git", root="/repo")
        run_git.side_effect = [
            _completed("abc123\n"),
            _completed("diff --git a/app.py b/app.py\n"),
        ]

        diff = get_diff(repo, "branch", "main")

        self.assertEqual(
            diff,
            "diff --git a/app.py b/app.py\n\n"
            "diff --git a/new.txt b/new.txt\n",
        )
        self.assertEqual(
            run_git.call_args_list,
            [
                unittest.mock.call(repo, ["merge-base", "main", "HEAD"]),
                unittest.mock.call(repo, ["diff", "abc123"]),
            ],
        )
        get_untracked.assert_called_once_with(repo)

    @patch(
        "agentreview.git.diff._get_untracked_files_diff",
        return_value="diff --git a/new.txt b/new.txt",
    )
    @patch("agentreview.git.diff._run_git")
    def test_commit_mode_includes_uncommitted_and_untracked_changes(self, run_git, get_untracked) -> None:
        repo = Repository(kind="git", root="/repo")
        run_git.return_value = _completed("diff --git a/app.py b/app.py\n")

        diff = get_diff(repo, "commit", "abc123")

        self.assertEqual(
            diff,
            "diff --git a/app.py b/app.py\n\n"
            "diff --git a/new.txt b/new.txt\n",
        )
        run_git.assert_called_once_with(repo, ["diff", "abc123"])
        get_untracked.assert_called_once_with(repo)

    @patch(
        "agentreview.git.diff._get_untracked_files_diff",
        return_value="diff --git a/new.txt b/new.txt",
    )
    @patch("agentreview.git.diff._run_sl")
    def test_sl_branch_mode_includes_uncommitted_and_untracked_changes(self, run_sl, get_untracked) -> None:
        repo = Repository(kind="sl", root="/repo")
        run_sl.side_effect = [
            _completed("1234567890abcdef\n", args=["sl"]),
            _completed("abcdef1234567890\n", args=["sl"]),
            _completed("diff --git a/app.py b/app.py\n", args=["sl"]),
        ]

        diff = get_diff(repo, "branch", "default")

        self.assertEqual(
            diff,
            "diff --git a/app.py b/app.py\n\n"
            "diff --git a/new.txt b/new.txt\n",
        )
        self.assertEqual(
            run_sl.call_args_list,
            [
                unittest.mock.call(repo, ["log", "-r", "default", "--template", "{node}"]),
                unittest.mock.call(repo, ["log", "-r", "ancestor(., 1234567890abcdef)", "--template", "{node}"]),
                unittest.mock.call(repo, ["diff", "--git", "-r", "abcdef1234567890"]),
            ],
        )
        get_untracked.assert_called_once_with(repo)

    @patch(
        "agentreview.git.diff._get_untracked_files_diff",
        return_value="diff --git a/new.txt b/new.txt",
    )
    @patch("agentreview.git.diff._run_sl")
    def test_sl_commit_mode_uses_rev_flag(self, run_sl, get_untracked) -> None:
        repo = Repository(kind="sl", root="/repo")
        run_sl.return_value = _completed("diff --git a/app.py b/app.py\n", args=["sl"])

        diff = get_diff(repo, "commit", "abc123")

        self.assertEqual(
            diff,
            "diff --git a/app.py b/app.py\n\n"
            "diff --git a/new.txt b/new.txt\n",
        )
        run_sl.assert_called_once_with(repo, ["diff", "--git", "-r", "abc123"])
        get_untracked.assert_called_once_with(repo)


class HelpTextTests(unittest.TestCase):
    def test_help_includes_examples_and_common_use_cases(self) -> None:
        result = CliRunner().invoke(main, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Examples:", result.output)
        self.assertIn("agentreview --branch main", result.output)
        self.assertIn("agentreview --commit HEAD~3", result.output)
        self.assertIn("Common use cases:", result.output)
        self.assertIn("git add -p && agentreview --staged", result.output)
        self.assertIn("--verbose", result.output)
        self.assertIn("--staged is only available in Git repositories.", result.output)
        self.assertIn("Use only one of --staged, --branch, or --commit.", result.output)
        self.assertIn("COMMIT can be any git commit-ish or Sapling revision identifier.", result.output)
        self.assertIn("https://agentreview-web.vercel.app/", result.output)


class PayloadEncodingTests(unittest.TestCase):
    def test_write_payload_matches_encode_payload(self) -> None:
        meta = PayloadMeta(
            repo="agentreview",
            branch="main",
            commit_hash="abc123",
            commit_message="Test commit",
            timestamp="2026-03-16T00:00:00+00:00",
            diff_mode="commit",
            base_commit="abc123",
        )

        payload = AgentReviewPayload(
            meta=meta,
            files=[
                AgentReviewFile(
                    path="app.py",
                    status="modified",
                    diff="diff --git a/app.py b/app.py\n",
                    source="print('hello')\n",
                    language="python",
                )
            ],
        )

        output = StringIO()
        write_payload(payload, output)

        self.assertEqual(output.getvalue(), encode_payload(payload))


class CliModeValidationTests(unittest.TestCase):
    def test_rejects_multiple_diff_modes(self) -> None:
        result = CliRunner().invoke(main, ["--branch", "main", "--commit", "abc123"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Choose only one of --staged, --branch, or --commit.", result.output)

    @patch("agentreview.cli.detect_repository", return_value=Repository(kind="sl", root="/repo"))
    def test_rejects_staged_mode_for_sl_repositories(self, detect_repository) -> None:
        result = CliRunner().invoke(main, ["--staged"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("--staged is only available in Git repositories.", result.output)
        detect_repository.assert_called_once_with(verbose=False)

    @patch("agentreview.cli.get_diff")
    @patch("agentreview.cli.detect_repository", return_value=Repository(kind="sl", root="/repo"))
    def test_surfaces_sl_stderr_when_diff_fails(self, detect_repository, get_diff_mock) -> None:
        get_diff_mock.side_effect = subprocess.CalledProcessError(
            255,
            ["sl", "diff"],
            stderr="abort: unknown revision 'abc123'",
        )

        result = CliRunner().invoke(main, ["--commit", "abc123"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error running sl diff: abort: unknown revision 'abc123'", result.output)
        detect_repository.assert_called_once_with(verbose=False)

    @patch("agentreview.cli.get_file_contents", return_value=[])
    @patch(
        "agentreview.cli.get_metadata",
        return_value=PayloadMeta(
            repo="agentreview",
            branch="main",
            commit_hash="abc123",
            commit_message="Test commit",
            timestamp="2026-03-16T00:00:00+00:00",
            diff_mode="commit",
            base_commit="abc123",
        ),
    )
    @patch("agentreview.cli.get_diff", return_value="diff --git a/app.py b/app.py\n")
    @patch(
        "agentreview.cli.detect_repository",
        return_value=Repository(kind="git", root="/repo", verbose=True),
    )
    def test_verbose_flag_emits_progress_messages(
        self,
        detect_repository,
        get_diff_mock,
        get_metadata_mock,
        get_file_contents_mock,
    ) -> None:
        result = CliRunner().invoke(main, ["-v", "--commit", "abc123"])

        self.assertEqual(result.exit_code, 0)
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] mode=commit base=abc123"),
        )
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] diff bytes="),
        )
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] collecting metadata"),
        )
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] metadata repo=agentreview branch=main commit=abc123"),
        )
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] extracting file contents"),
        )
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] files=0"),
        )
        self.assertRegex(
            result.output,
            re.compile(r"\[agentreview [^\]]+\] writing payload"),
        )
        detect_repository.assert_called_once_with(verbose=True)
        get_diff_mock.assert_called_once()
        get_metadata_mock.assert_called_once()
        get_file_contents_mock.assert_called_once()


class MetadataTests(unittest.TestCase):
    @patch("agentreview.git.metadata._sl")
    def test_sl_metadata_uses_bookmark_and_remote_name(self, sl) -> None:
        repo = Repository(kind="sl", root="/repo/project")
        sl.side_effect = [
            "ssh://sl@example.com/team/project",
            "feature-bookmark",
            "abc123",
            "Add sl support",
        ]

        meta = get_metadata(repo, "branch", "default")

        self.assertEqual(meta.repo, "project")
        self.assertEqual(meta.branch, "feature-bookmark")
        self.assertEqual(meta.commit_hash, "abc123")
        self.assertEqual(meta.commit_message, "Add sl support")
