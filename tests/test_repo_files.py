"""Repository files: manifest and hacs.json, translations, services.yaml, README,
examples/ and CI.

These checks stand in for what hassfest and the HACS action look at, so a broken
file shows up in a local run too. The examples are validated with Home
Assistant's own automation, script and helper schemas.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re
from typing import Any

from awesomeversion import AwesomeVersion
import pytest
import yaml

from homeassistant import loader
from homeassistant.components import input_boolean, input_number
from homeassistant.components.automation import config as automation_config
from homeassistant.components.script import config as script_config
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml

from custom_components.electros_ev import protocol

from .common import ENTITY_OBJECT_IDS

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "electros_ev"
EXAMPLES = ROOT / "examples"
EXAMPLE_LOGIN = "es123456"
LANGUAGES = ("en", "uk", "ru")

#: Manifest keys the Home Assistant loader knows (hassfest's schema is the same set).
MANIFEST_KEYS = set(loader.Manifest.__annotations__) - {"is_built_in", "overwrites_built_in"}

ENTITY_ID_RE = re.compile(r"\b(sensor|switch|number|button|time)\.electros_([a-z0-9_]+)")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest() -> dict[str, Any]:
    return _json(COMPONENT / "manifest.json")


def _strings() -> dict[str, Any]:
    return _json(COMPONENT / "strings.json")


def _services_yaml() -> dict[str, Any]:
    return yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


# --------------------------------------------------------------------------- #
# manifest.json and hacs.json
# --------------------------------------------------------------------------- #


def test_manifest_has_what_hassfest_and_hacs_require() -> None:
    manifest = _manifest()
    assert manifest["domain"] == "electros_ev"
    for key in ("name", "codeowners", "documentation", "issue_tracker", "iot_class", "version"):
        assert key in manifest, key
    assert manifest["config_flow"] is True
    assert (COMPONENT / "config_flow.py").is_file()
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []
    # The Lovelace card is served over http and added to the frontend.
    assert {"http", "frontend"} <= set(manifest["dependencies"])
    assert AwesomeVersion(manifest["version"]).valid


def test_manifest_has_only_known_keys() -> None:
    """hassfest's manifest schema allows no extra keys (upstream's 'icon' is gone)."""
    assert set(_manifest()) <= MANIFEST_KEYS


def test_manifest_keys_are_sorted_for_hassfest() -> None:
    """hassfest wants domain, name, then the other keys in alphabetical order."""
    keys = list(_manifest())
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_hacs_json() -> None:
    hacs = _json(ROOT / "hacs.json")
    assert set(hacs) == {"name", "homeassistant", "render_readme"}
    assert hacs["name"] == _manifest()["name"]
    assert hacs["render_readme"] is True
    minimum = AwesomeVersion(hacs["homeassistant"])
    # OptionsFlow.config_entry without passing it, DataUpdateCoordinator(config_entry=)
    # and ConfigEntry.runtime_data need 2024.11; the tests run on the pinned version.
    assert minimum >= AwesomeVersion("2024.11.0")
    assert minimum <= AwesomeVersion(HA_VERSION)


def test_card_version_matches_the_manifest() -> None:
    card = (COMPONENT / "frontend" / "electros-card.js").read_text(encoding="utf-8")
    found = re.search(r"const VERSION = '([^']+)'", card)
    assert found is not None
    assert found.group(1) == _manifest()["version"]


# --------------------------------------------------------------------------- #
# Translations and services.yaml
# --------------------------------------------------------------------------- #


def test_strings_json_is_the_english_translation() -> None:
    assert _json(COMPONENT / "translations" / "en.json") == _strings()


@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_have_every_key_and_placeholder(language: str) -> None:
    source = _flatten(_strings())
    translated = _flatten(_json(COMPONENT / "translations" / f"{language}.json"))
    assert set(translated) == set(source)
    for key, text in source.items():
        placeholders = set(re.findall(r"\{(\w+)\}", str(text)))
        assert set(re.findall(r"\{(\w+)\}", str(translated[key]))) == placeholders, key
        assert str(translated[key]).strip(), key


def test_services_yaml_matches_the_translations() -> None:
    services = _services_yaml()
    strings = _strings()["services"]
    assert set(services) == set(strings)
    for name, service in services.items():
        assert strings[name]["name"] and strings[name]["description"], name
        fields = set((service or {}).get("fields") or {})
        assert set(strings[name].get("fields") or {}) == fields, name
        for field in fields:
            assert strings[name]["fields"][field]["name"], (name, field)
            assert strings[name]["fields"][field]["description"], (name, field)
        assert "device_id" in fields
        assert service["fields"]["device_id"]["required"] is True


def test_status_states_are_translated() -> None:
    states = _strings()["entity"]["sensor"]["status"]["state"]
    assert list(states) == list(protocol.STATUS_OPTIONS)


def test_error_and_exception_keys_used_in_code_are_translated() -> None:
    strings = _strings()
    code = "\n".join(path.read_text(encoding="utf-8") for path in COMPONENT.glob("*.py"))
    used_exceptions = set(re.findall(r'translation_key="([a-z_]+)"', code))
    assert used_exceptions == set(strings["exceptions"])
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    used_errors = set(re.findall(r'errors\[[^\]]+\] = "([a-z_]+)"', flow))
    known = set(strings["config"]["error"]) | set(strings["options"]["error"])
    assert used_errors and used_errors <= known


# --------------------------------------------------------------------------- #
# README.md
# --------------------------------------------------------------------------- #


def test_readme_documents_every_entity_and_service() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for (domain, _key), object_id in ENTITY_OBJECT_IDS.items():
        assert f"{domain}.electros_<login>_{object_id}" in readme, object_id
    for service in _services_yaml():
        assert f"electros_ev.{service}" in readme, service
    for option in ("tariff", "23:00", "07:00", "4,32", "2,16"):
        assert option in readme
    assert AwesomeVersion(_json(ROOT / "hacs.json")["homeassistant"]).section(1) == 11
    assert "2024.11" in readme


def test_readme_links_point_to_existing_files() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    visible = re.sub(r"<!--.*?-->", "", readme, flags=re.DOTALL)  # screenshot placeholders
    links = re.findall(r"\]\((?!https?://|#)([^)\s]+)\)", visible)
    assert links
    for link in links:
        assert (ROOT / link).exists(), link


# --------------------------------------------------------------------------- #
# examples/
# --------------------------------------------------------------------------- #


def _examples() -> dict[str, Any]:
    return {path.name: load_yaml(path) for path in sorted(EXAMPLES.glob("*.yaml"))}


def test_examples_use_real_entity_ids() -> None:
    known = {(domain, object_id) for (domain, _), object_id in ENTITY_OBJECT_IDS.items()}
    found = set()
    for path in EXAMPLES.glob("*.yaml"):
        for domain, rest in ENTITY_ID_RE.findall(path.read_text(encoding="utf-8")):
            assert rest.startswith(f"{EXAMPLE_LOGIN}_"), f"{path.name}: {domain}.electros_{rest}"
            entity = (domain, rest.removeprefix(f"{EXAMPLE_LOGIN}_"))
            assert entity in known, f"{path.name}: {domain}.electros_{rest}"
            found.add(entity)
    assert len(found) >= 10


def test_examples_call_real_services_with_valid_fields() -> None:
    services = _services_yaml()
    calls = 0
    for name, data in _examples().items():
        for node in _walk(data):
            if not isinstance(node, dict) or not str(node.get("action", "")).startswith(
                "electros_ev."
            ):
                continue
            service = node["action"].removeprefix("electros_ev.")
            assert service in services, f"{name}: {service}"
            fields = set(services[service]["fields"])
            assert set(node["data"]) <= fields, f"{name}: {service} {node['data']}"
            assert "device_id" in node["data"], f"{name}: {service}"
            calls += 1
    assert calls >= 6


async def test_example_automations_are_valid(hass: HomeAssistant) -> None:
    automations = load_yaml(EXAMPLES / "automations.yaml")
    assert isinstance(automations, list) and len(automations) >= 6
    ids = [item["id"] for item in automations]
    assert len(set(ids)) == len(ids)
    for item in automations:
        validated = await automation_config.async_validate_config_item(hass, item["id"], item)
        assert validated is not None, item["id"]
        assert validated.validation_error is None, (item["id"], validated.validation_error)
    text = (EXAMPLES / "automations.yaml").read_text(encoding="utf-8")
    assert "mobile_app_notification_action" in text
    assert "Авто підключено вдень — зарядити 30 хв / 10 кВт·год?" in text


async def test_example_scripts_and_helpers_are_valid(hass: HomeAssistant) -> None:
    scripts = load_yaml(EXAMPLES / "scripts.yaml")
    for object_id, item in scripts.items():
        validated = await script_config.async_validate_config_item(hass, object_id, item)
        assert validated is not None, object_id
        assert validated.validation_error is None, (object_id, validated.validation_error)

    helpers = load_yaml(EXAMPLES / "configuration.yaml")
    input_boolean.CONFIG_SCHEMA({"input_boolean": helpers["input_boolean"]})
    input_number.CONFIG_SCHEMA({"input_number": helpers["input_number"]})

    # Every helper and script the examples refer to is defined in examples/.
    text = "\n".join(path.read_text(encoding="utf-8") for path in EXAMPLES.glob("*.yaml"))
    for domain, defined in (
        ("input_boolean", helpers["input_boolean"]),
        ("input_number", helpers["input_number"]),
        ("script", scripts),
    ):
        for object_id in re.findall(rf"\b{domain}\.([a-z0-9_]+)(?![\w*])", text):
            assert object_id in defined, f"{domain}.{object_id}"


def test_example_dashboard_uses_the_card_options() -> None:
    dashboard = load_yaml(EXAMPLES / "dashboard.yaml")
    card_options = {
        "custom:electros-card": {
            "type", "device_id", "theme", "quick_minutes", "quick_kwh", "stats_period", "charge_mode"
        },
        "custom:electros-tile": {"type", "device_id", "theme", "quick_minutes", "size"},
    }
    seen = set()
    for node in _walk(dashboard):
        if isinstance(node, dict) and node.get("type") in card_options:
            assert set(node) <= card_options[node["type"]], node
            assert re.fullmatch(r"[0-9a-f]{32}", node["device_id"])
            seen.add(node["type"])
    assert seen == set(card_options)


# --------------------------------------------------------------------------- #
# CI and repository settings
# --------------------------------------------------------------------------- #


def test_ci_workflow() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    steps = {name: job["steps"] for name, job in jobs.items()}
    assert all(job["runs-on"] == "ubuntu-latest" for job in jobs.values())

    pytest_steps = steps["pytest"]
    python = next(s for s in pytest_steps if s.get("uses", "").startswith("actions/setup-python"))
    assert python["with"]["python-version"] == "3.13"
    commands = " ".join(s.get("run", "") for s in pytest_steps)
    assert "pip install -r requirements_test.txt" in commands
    assert "pytest" in commands

    assert any(s.get("uses") == "home-assistant/actions/hassfest@master" for s in steps["hassfest"])
    hacs = next(s for s in steps["hacs"] if s.get("uses") == "hacs/action@main")
    assert hacs["with"]["category"] == "integration"


def test_requirements_pin_the_harness_used_locally() -> None:
    lines = [
        line.strip()
        for line in (ROOT / "requirements_test.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    harness = "pytest-homeassistant-custom-component"
    assert f"{harness}=={package_version(harness)}" in lines


def test_gitattributes_uses_lf() -> None:
    assert (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines() == [
        "* text=auto eol=lf"
    ]
