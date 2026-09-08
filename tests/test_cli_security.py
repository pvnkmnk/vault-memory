"""Security unit tests for CLI commands."""

from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from cli.main import cli


def test_heartbeat_subprocess_uses_argument_separator(tmp_path):
    """Test that heartbeat command uses '--' positional argument separator for bash."""
    runner = CliRunner()

    bridge_dir = tmp_path / "homelab-bridge"
    bridge_dir.mkdir(parents=True)
    script_file = bridge_dir / "heartbeat.sh"
    script_file.write_text("#!/bin/bash\necho ok")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        result = runner.invoke(cli, ["heartbeat", "--mode", "daily", "--vault", str(tmp_path)])

        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        cmd = args[0]

        assert cmd[0] == "bash"
        assert cmd[1] == "--"
        assert cmd[2] == str(script_file)
        assert cmd[3] == "--mode=daily"
        assert kwargs.get("cwd") == str(tmp_path)
