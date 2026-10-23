import os
import subprocess
import sys

PRINT_DATA_DIR = "import ikg.cli; from ikg import config; print(config.DATA_DIR)"


def run_in(cwd, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k != "IKG_DATA"}
    env.update(extra_env or {})
    out = subprocess.run([sys.executable, "-c", PRINT_DATA_DIR], cwd=cwd, env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_dotenv_in_working_directory_is_loaded(tmp_path):
    (tmp_path / ".env").write_text("IKG_DATA=from_dotenv\n", encoding="utf-8")
    assert run_in(tmp_path) == "from_dotenv"


def test_shell_environment_overrides_dotenv(tmp_path):
    (tmp_path / ".env").write_text("IKG_DATA=from_dotenv\n", encoding="utf-8")
    assert run_in(tmp_path, {"IKG_DATA": "from_shell"}) == "from_shell"
