import json
from pathlib import Path

from blindfold.config import Config
from blindfold.protocol import find_activation

ROOT = Path(__file__).resolve().parents[1]


def test_skill_has_marker_activation_and_rules():
    text = (ROOT / "src/blindfold/skill/SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
    assert "blindfold-activation depth=$0" in text
    assert "!`blindfold activate $0 ${CLAUDE_SESSION_ID}`" in text
    assert "blindfold schema" in text and "[Person_1]" in text
    assert find_activation([{"role": "user", "content": text.replace("$0", "strict")}])[0] == "strict"


def test_v11_skill_forbids_mentioning_the_masking():
    text = (ROOT / "src/blindfold/skill/SKILL.md").read_text(encoding="utf-8")
    assert ("Never tell the user that values are hidden, redacted, masked or replaced, and never mention "
            "placeholders or blindfold in your answer. Write as if you see the real values; the user sees them."
            ) in text


def test_managed_settings_points_at_proxy_and_locks_channels():
    ms = json.loads((ROOT / "deploy/managed-settings.json").read_text(encoding="utf-8"))
    assert ms["env"]["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{Config().port}"
    for key in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING",
                "DISABLE_FEEDBACK_COMMAND", "DISABLE_UPDATES", "CLAUDE_CODE_DISABLE_ARTIFACT",
                "CLAUDE_CODE_ATTRIBUTION_HEADER"):
        assert key in ms["env"]
    assert ms["cleanupPeriodDays"] == 7 and ms["showThinkingSummaries"] is False
    hook = ms["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Read|Bash|PowerShell" and hook["hooks"][0]["args"] == ["hook"]


def test_docs_have_no_em_dashes():
    for p in [ROOT / "src/blindfold/skill/SKILL.md", ROOT / "deploy/install.ps1"]:
        assert "—" not in p.read_text(encoding="utf-8"), p


def test_i1_installer_defaults_ner_off_and_prefetches_model():
    ps = (ROOT / "deploy/install.ps1").read_text(encoding="utf-8")
    assert '"use_ner": false' in ps
    assert "GLiNER2.from_pretrained('fastino/gliner2-multi-v1')" in ps
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '"use_ner": true' in readme and "\u2014" not in readme
