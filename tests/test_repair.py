"""Counterexamples for repair gates, including interrupted two-asset switches."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from nl2repo_factory.repair import RepairTransaction, validate_repair


class RepairGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.canonical = self.root / "canonical"
        self.canonical.mkdir()
        self.candidate = self.root / "candidate"
        self.config = {
            "pro_name": "TagKit", "test_case_count": 8,
            "test_commands": ["python -m pytest -q"], "test_files": ["tests/test_api.py"],
            "package_files": [], "fixture_files": [],
        }
        for name, data in {
            "environment/start.md": "Frozen instructions\n",
            "environment/api_manifest.json": "{}\n",
            "instruction.md": "See start.md\n",
            "environment/Dockerfile": "FROM python:3.11\nWORKDIR /workspace\n",
            "task.toml": '[environment]\ndocker_image_archive = "/images/task.tar"\n',
            "tests/config.json": json.dumps(self.config),
            "tests/reference/tests/test_api.py": "def test_normalize():\n    assert normalize('A') == ['a']\n",
            "tests/reference/tests/conftest.py": "import pytest\n",
            "tests/reference/pkg/api_test.py": "def test_api():\n    assert api() == 3\n",
            "solution/solve.sh": "#!/bin/sh\ncp -r /solution/reference/. /workspace/\n",
        }.items():
            path = self.canonical / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        shutil.copytree(self.canonical, self.candidate)

    def response(self, changed=None, status="repaired", image_commands=None):
        return {
            "schema_version": "teamfactory.oracle_repair.v1", "status": status,
            "responsible_stage": "stage2_ast", "root_cause": "count mismatch",
            "diagnosis": "The observed count differs from the configured count.",
            "evidence": ["trusted report"], "validation_notes": ["Run Oracle again"],
            "changed_files": changed or [], "image_commands": image_commands or [],
        }

    def configure(self, **changes):
        config = self.config | changes
        (self.candidate / "tests/config.json").write_text(json.dumps(config))

    def gate(self, changed=None, report=None, **response):
        return validate_repair(self.canonical, self.candidate, self.response(changed, **response), report)

    def assert_rejected(self, result, fragment):
        self.assertFalse(result["passed"], result)
        self.assertIn(fragment, "\n".join(result["errors"]))

    def test_count_decrease_requires_trusted_full_pass(self):
        self.configure(test_case_count=4)
        result = self.gate(["tests/config.json"], {"observed_total": 4, "passed": 4, "failed": 0, "errors": 0})
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["changed_files"], ["tests/config.json"])
        for report in (None, {}, {"observed_total": 4, "passed": 3, "failed": 1, "errors": 0},
                       {"observed_total": 5, "passed": 5, "failed": 0, "errors": 0},
                       {"observed_total": 4, "passed": 4, "failed": False, "errors": 0}):
            with self.subTest(report=report):
                self.assert_rejected(self.gate(["tests/config.json"], report), "trusted all-passed")

    def test_agent_cannot_self_supply_trusted_report(self):
        self.configure(test_case_count=4)
        response = self.response(["tests/config.json"])
        response["trusted_report"] = {"observed_total": 4, "passed": 4, "failed": 0, "errors": 0}
        self.assert_rejected(validate_repair(self.canonical, self.candidate, response), "trusted all-passed")

    def test_positive_integer_denominator_required(self):
        for count in (0, -1, True, 4.0, "4"):
            with self.subTest(count=count):
                self.configure(test_case_count=count)
                self.assert_rejected(self.gate(["tests/config.json"]), "positive integer")

    def test_extra_command_flags_preserve_old_tokens(self):
        self.configure(test_commands=["python -m pytest -q -vv"])
        result = self.gate(["tests/config.json"])
        self.assertTrue(result["passed"], result)

    def test_old_commands_all_must_survive_in_order(self):
        for commands in (["python -m pytest -vv"], ["pytest -q -m python"], [], ["echo 4 passed"]):
            with self.subTest(commands=commands):
                self.configure(test_commands=commands)
                self.assert_rejected(self.gate(["tests/config.json"]), "token sequence")

    def test_bypass_rejected_even_when_tokens_preserved(self):
        for suffix in (" || true", "; exit 0", "; printf ok", "; echo ok", " > /logs/verifier/result",
                       " > reward.txt", " || :"):
            with self.subTest(suffix=suffix):
                self.configure(test_commands=["python -m pytest -q" + suffix])
                self.assert_rejected(self.gate(["tests/config.json"]), "forbidden reward")

    def test_malformed_shell_is_reported(self):
        self.configure(test_commands=["python -m pytest -q '"])
        self.assert_rejected(self.gate(["tests/config.json"]), "invalid test configuration")

    def test_frozen_files_and_test_sources(self):
        names = ["environment/start.md", "environment/api_manifest.json", "instruction.md",
                 "tests/reference/tests/test_api.py", "tests/reference/tests/conftest.py", "tests/reference/pkg/api_test.py"]
        for name in names:
            with self.subTest(name=name):
                target = self.candidate / name
                old = target.read_text()
                target.write_text(old + "\n")
                self.assert_rejected(self.gate([name]), "frozen")
                target.write_text(old)

    def test_test_source_deletion_rejected(self):
        (self.candidate / "tests/reference/tests/test_api.py").unlink()
        self.assert_rejected(self.gate(["tests/reference/tests/test_api.py"]), "frozen test source")

    def test_test_list_cannot_remove_unchanged_source(self):
        self.configure(test_files=[])
        self.assert_rejected(self.gate(["tests/config.json"]), "cannot remove")

    def test_explicit_test_nonstandard_filename_frozen(self):
        self.config["test_files"].append("checks.py")
        for root in (self.canonical, self.candidate):
            (root / "tests/config.json").write_text(json.dumps(self.config))
            (root / "tests/reference/checks.py").write_text("def test_ok():\n    assert ok()\n")
        (self.candidate / "tests/reference/checks.py").write_text("def test_ok():\n    assert True\n")
        self.assert_rejected(self.gate(["tests/reference/checks.py"]), "frozen test source")

    def test_added_skip_xfail_or_trivial_assertions_rejected(self):
        name = "tests/reference/tests/test_extra.py"
        for code, fragment in (
            ("import pytest\n@pytest.mark.skip\ndef test_ok():\n    assert api()\n", "skip or xfail"),
            ("import pytest\ndef test_ok():\n    pytest.xfail('broken')\n", "skip or xfail"),
            ("def test_ok():\n    assert True\n", "unconditional"),
            ("def test_ok():\n    assert 1 == 1\n", "unconditional"),
            ("def test_ok(self):\n    self.assertEqual(2, 2)\n", "unconditional"),
        ):
            with self.subTest(code=code):
                (self.candidate / name).write_text(code)
                self.assert_rejected(self.gate([name]), fragment)

    def test_exact_changes_include_deleted_added_and_permission_changes(self):
        name = "solution/solve.sh"
        (self.candidate / name).chmod(0o700)
        self.assert_rejected(self.gate(["tests/config.json"]), "exactly match")
        self.assertTrue(self.gate([name])["passed"])
        (self.candidate / name).unlink()
        (self.candidate / "solution/new.sh").write_text("true\n")
        self.assertEqual(self.gate([name, "solution/new.sh"])["changed_files"], ["solution/new.sh", name])

    def test_duplicate_declared_changes_rejected(self):
        self.configure(test_commands=["python -m pytest -q -vv"])
        self.assert_rejected(self.gate(["tests/config.json", "tests/config.json"]), "without duplicates")

    def test_unrepairable_must_really_leave_no_changes(self):
        self.assertTrue(self.gate(status="unrepairable")["passed"])
        (self.candidate / "unreported.txt").write_text("hidden change")
        self.assert_rejected(self.gate(status="unrepairable"), "no declared or actual changes")

    def test_unrepairable_rejects_image_commands(self):
        self.assert_rejected(self.gate(status="unrepairable", image_commands=["pip install x"]), "no image_commands")

    def test_unrepairable_rejects_empty_directory_changes(self):
        (self.candidate / "extra").mkdir()
        self.assert_rejected(self.gate(status="unrepairable"), "no directory")

    def test_repaired_requires_changes(self):
        self.assert_rejected(self.gate(), "nonempty changed_files")

    def test_response_schema_and_attribution_required(self):
        response = self.response(status="unrepairable")
        del response["responsible_stage"]
        response["schema_version"] = "wrong"
        result = validate_repair(self.canonical, self.candidate, response)
        self.assert_rejected(result, "schema_version")
        self.assertIn("responsible_stage", " ".join(result["errors"]))

    def test_malformed_response_types_fail_closed(self):
        for field, value in (("status", []), ("responsible_stage", {}), ("changed_files", [3]),
                             ("image_commands", "command"), ("evidence", None)):
            with self.subTest(field=field):
                response = self.response(status="unrepairable")
                response[field] = value
                self.assertFalse(validate_repair(self.canonical, self.candidate, response)["passed"])

    def test_missing_frozen_link_target_is_reported(self):
        for root in (self.canonical, self.candidate):
            (root / "instruction.md").unlink()
            (root / "instruction.md").symlink_to("missing.txt")
        self.configure(test_commands=["python -m pytest -q -vv"])
        self.assert_rejected(self.gate(["tests/config.json"]), "cannot read frozen")

    def test_escaping_symlink_rejected(self):
        (self.candidate / "outside").symlink_to(self.root)
        self.assert_rejected(self.gate(["outside"]), "symlink escapes")

    def test_internal_symlink_allowed(self):
        (self.candidate / "instruction-link").symlink_to("instruction.md")
        result = self.gate(["instruction-link"])
        self.assertTrue(result["passed"], result)

    def test_frozen_file_cannot_hide_change_through_symlink(self):
        for root in (self.canonical, self.candidate):
            (root / "payload.txt").write_text("same")
            (root / "instruction.md").unlink()
            (root / "instruction.md").symlink_to("payload.txt")
        (self.candidate / "payload.txt").write_text("changed")
        self.assert_rejected(self.gate(["payload.txt"]), "frozen task file target")

    def test_archive_destination_cannot_change(self):
        (self.candidate / "task.toml").write_text('[environment]\ndocker_image_archive="/images/other.tar"\n')
        self.assert_rejected(self.gate(["task.toml"]), "archive target")

    def test_valid_declared_persistent_run(self):
        dockerfile = self.candidate / "environment/Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "RUN python -m pip install pytest==8.2.2\n")
        result = self.gate(["environment/Dockerfile"], image_commands=["python -m pip install pytest==8.2.2"])
        self.assertTrue(result["passed"], result)

    def test_dockerfile_and_commands_must_match(self):
        dockerfile = self.candidate / "environment/Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "RUN python -m pip install pytest\n")
        self.assert_rejected(self.gate(["environment/Dockerfile"]), "occur together")
        self.assert_rejected(self.gate(["environment/Dockerfile"], image_commands=["pip install something"]), "exactly the declared RUN")

    def test_commands_without_dockerfile_rejected(self):
        self.assert_rejected(self.gate(image_commands=["python -m pip install pytest"]), "occur together")

    def test_dockerfile_link_cannot_hide_image_layer_change(self):
        for root in (self.canonical, self.candidate):
            (root / "environment/Dockerfile").rename(root / "environment/Dockerfile.source")
            (root / "environment/Dockerfile").symlink_to("Dockerfile.source")
        source = self.candidate / "environment/Dockerfile.source"
        source.write_text(source.read_text() + "RUN python -m pip install pytest\n")
        self.assert_rejected(self.gate(["environment/Dockerfile.source"]), "occur together")

    def test_dockerfile_cannot_replace_existing_layers(self):
        (self.candidate / "environment/Dockerfile").write_text("FROM unexpected\nRUN python -m pip install pytest\n")
        self.assert_rejected(self.gate(["environment/Dockerfile"], image_commands=["python -m pip install pytest"]), "exactly the declared RUN")

    def test_image_commands_cannot_mount_or_read_private_assets(self):
        dockerfile = self.candidate / "environment/Dockerfile"
        original = dockerfile.read_text()
        for command in ("docker run image", "mount /dev/sda /mnt", "cp /solution/code /workspace",
                        "cat /tests/test.py", "podman run image", "true\nfalse", "echo ok"):
            with self.subTest(command=command):
                dockerfile.write_text(original + "RUN " + command + "\n")
                self.assert_rejected(self.gate(["environment/Dockerfile"], image_commands=[command]), "single-line")

    def test_paths_cannot_escape_reference(self):
        self.configure(test_files=["tests/test_api.py", "../secret"])
        self.assert_rejected(self.gate(["tests/config.json"]), "invalid relative path")


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.instance = self.root / "instance"
        self.candidate = self.root / "candidate"
        self.instance.mkdir()
        self.candidate.mkdir()
        (self.instance / "value").write_text("old")
        (self.candidate / "value").write_text("new")
        self.image = self.root / "image.tar"
        self.new_image = self.root / "new-image.tar"
        self.image.write_bytes(b"old image archive")
        self.new_image.write_bytes(b"new image archive")
        self.journal = self.root / "repair.json"
        self.transaction = RepairTransaction(self.instance, self.image, self.journal)

    def reopen(self):
        return RepairTransaction(self.instance, self.image, self.journal)

    def assert_old(self):
        self.assertEqual((self.instance / "value").read_text(), "old")
        self.assertEqual(self.image.read_bytes(), b"old image archive")

    def assert_new(self):
        self.assertEqual((self.instance / "value").read_text(), "new")
        self.assertEqual(self.image.read_bytes(), b"new image archive")

    def test_switch_then_commit_preserves_candidate_inputs(self):
        self.transaction.switch(self.candidate, self.new_image)
        self.assert_new()
        self.assertEqual(self.transaction.state, "switched")
        self.assertEqual((self.candidate / "value").read_text(), "new")
        self.assertEqual(self.new_image.read_bytes(), b"new image archive")
        self.transaction.commit()
        self.assert_new()
        self.assertEqual(self.reopen().state, "committed")
        self.assertFalse(list(self.root.glob(".*.repair-*")))
        self.transaction.commit()

    def test_switch_then_reopen_and_rollback_restores_both(self):
        self.transaction.switch(self.candidate, self.new_image)
        recovered = self.reopen()
        self.assertEqual(recovered.state, "switched")
        recovered.rollback()
        self.assert_old()
        self.assertEqual(self.reopen().state, "rolled_back")
        self.assertFalse(list(self.root.glob(".*.repair-*")))
        recovered.rollback()

    def test_switch_failure_automatically_restores_both_assets(self):
        import os
        replace = os.replace

        def failing_replace(source, destination):
            if Path(destination) == self.image and str(source).endswith(".staged"):
                raise OSError("injected image installation failure")
            return replace(source, destination)

        with mock.patch("nl2repo_factory.repair.os.replace", side_effect=failing_replace):
            with self.assertRaisesRegex(OSError, "injected"):
                self.transaction.switch(self.candidate, self.new_image)
        self.assert_old()
        self.assertEqual(self.reopen().state, "rolled_back")

    def test_partial_backup_recovery_after_abrupt_process_loss(self):
        import os
        replace = os.replace

        def crash_after_instance_backup(source, destination):
            result = replace(source, destination)
            if Path(source) == self.instance:
                raise SystemExit("simulated process loss")
            return result

        with mock.patch("nl2repo_factory.repair.os.replace", side_effect=crash_after_instance_backup):
            with mock.patch.object(self.transaction, "rollback", side_effect=SystemExit("no cleanup during crash")):
                with self.assertRaises(SystemExit):
                    self.transaction.switch(self.candidate, self.new_image)
        self.assertFalse(self.instance.exists())
        self.reopen().rollback()
        self.assert_old()

    def test_recovery_from_every_switch_rename_boundary(self):
        import os
        real_replace = os.replace
        for boundary in range(4):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                instance, candidate = root / "instance", root / "candidate"
                instance.mkdir()
                candidate.mkdir()
                (instance / "value").write_text("old")
                (candidate / "value").write_text("new")
                image, new_image, journal = root / "old.tar", root / "new.tar", root / "journal.json"
                image.write_bytes(b"old")
                new_image.write_bytes(b"new")
                transaction = RepairTransaction(instance, image, journal)
                mutation = 0

                def crash(source, destination):
                    nonlocal mutation
                    result = real_replace(source, destination)
                    if Path(destination) != journal:
                        current = mutation
                        mutation += 1
                        if current == boundary:
                            raise SystemExit("power loss")
                    return result

                with mock.patch("nl2repo_factory.repair.os.replace", side_effect=crash):
                    with mock.patch.object(transaction, "rollback", side_effect=SystemExit("process gone")):
                        with self.assertRaises(SystemExit):
                            transaction.switch(candidate, new_image)
                recovered = RepairTransaction(instance, image, journal)
                recovered.rollback()
                self.assertEqual((instance / "value").read_text(), "old")
                self.assertEqual(image.read_bytes(), b"old")
                self.assertEqual(recovered.state, "rolled_back")

    def test_interrupted_rollback_can_be_resumed(self):
        import os
        real_replace = os.replace
        self.transaction.switch(self.candidate, self.new_image)

        def interrupt_after_candidate_displaced(source, destination):
            result = real_replace(source, destination)
            if Path(source) == self.instance:
                raise OSError("rollback interrupted after displacing candidate")
            return result

        with mock.patch("nl2repo_factory.repair.os.replace", side_effect=interrupt_after_candidate_displaced):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete"):
                self.transaction.rollback()
        self.assertFalse(self.instance.exists())
        self.reopen().rollback()
        self.assert_old()

    def test_staging_error_does_not_touch_originals(self):
        with mock.patch("nl2repo_factory.repair.shutil.copy2", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.transaction.switch(self.candidate, self.new_image)
        self.assert_old()
        self.assertEqual(self.reopen().state, "rolled_back")

    def test_interrupted_commit_can_be_completed(self):
        self.transaction.switch(self.candidate, self.new_image)
        original_remove = self.transaction._remove
        counter = 0

        def failing_remove(path):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise OSError("cleanup interrupted")
            original_remove(path)

        with mock.patch.object(self.transaction, "_remove", side_effect=failing_remove):
            with self.assertRaisesRegex(OSError, "cleanup interrupted"):
                self.transaction.commit()
        recovered = self.reopen()
        self.assertEqual(recovered.state, "committing")
        with self.assertRaisesRegex(RuntimeError, "commit has started"):
            recovered.rollback()
        recovered.commit()
        self.assert_new()
        self.assertEqual(self.reopen().state, "committed")

    def test_unrelated_directory_is_not_removed_by_rollback(self):
        self.transaction.switch(self.candidate, self.new_image)
        (self.instance / "unrelated.txt").write_text("do not delete")
        with self.assertRaisesRegex(RuntimeError, "outside this transaction"):
            self.transaction.rollback()
        self.assertEqual((self.instance / "unrelated.txt").read_text(), "do not delete")
        # The image is still restored, even though the instance requires manual resolution.
        self.assertEqual(self.image.read_bytes(), b"old image archive")
        self.assertEqual(self.reopen().state, "rolling_back")
        (self.instance / "unrelated.txt").unlink()
        self.reopen().rollback()
        self.assert_old()

    def test_unrelated_empty_directory_is_not_removed_by_rollback(self):
        self.transaction.switch(self.candidate, self.new_image)
        (self.instance / "unrelated-empty").mkdir()
        with self.assertRaisesRegex(RuntimeError, "outside this transaction"):
            self.transaction.rollback()
        self.assertTrue((self.instance / "unrelated-empty").is_dir())
        (self.instance / "unrelated-empty").rmdir()
        self.reopen().rollback()
        self.assert_old()

    def test_foreign_journal_rejected(self):
        self.transaction.switch(self.candidate, self.new_image)
        with self.assertRaisesRegex(ValueError, "different or malformed"):
            RepairTransaction(self.root / "other-instance", self.image, self.journal)

    def test_cannot_switch_again_until_pending_transaction_resolved(self):
        self.transaction.switch(self.candidate, self.new_image)
        with self.assertRaisesRegex(RuntimeError, "unfinished"):
            self.reopen().switch(self.candidate, self.new_image)
        self.transaction.rollback()
        self.transaction.switch(self.candidate, self.new_image)
        self.transaction.commit()
        self.assert_new()

    def test_overlapping_candidate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            self.transaction.switch(self.instance, self.new_image)
        self.assert_old()

    def test_journal_must_be_outside_instance(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            RepairTransaction(self.instance, self.image, self.instance / "journal.json")

    def test_commit_before_switch_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "cannot commit"):
            self.transaction.commit()
        self.transaction.rollback()
        self.assert_old()

    def test_original_absolute_internal_link_survives_backup_and_rollback(self):
        (self.instance / "link").symlink_to(self.instance / "value")
        self.transaction.switch(self.candidate, self.new_image)
        self.reopen().rollback()
        self.assert_old()
        self.assertTrue((self.instance / "link").is_symlink())
        self.assertEqual((self.instance / "link").read_text(), "old")

    def test_candidate_absolute_link_fails_before_switching(self):
        (self.candidate / "link").symlink_to(self.candidate / "value")
        with self.assertRaisesRegex(ValueError, "symlink escapes"):
            self.transaction.switch(self.candidate, self.new_image)
        self.assert_old()
        self.assertEqual(self.reopen().state, "rolled_back")

    def test_candidate_relative_link_survives_relocation(self):
        (self.candidate / "link").symlink_to("value")
        self.transaction.switch(self.candidate, self.new_image)
        self.transaction.commit()
        self.assertEqual((self.instance / "link").read_text(), "new")


if __name__ == "__main__":
    unittest.main()
