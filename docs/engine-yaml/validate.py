"""Check documentation examples, fenced YAML/JSON, and relative file links.

Run with this repository's engine virtual environment. No browser, database, or
external request is started. Complete examples are compiled; fragments are parsed.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parents[1]
if (ROOT / "rpa" / "src" / "engine").is_dir():
    sys.path.insert(0, str(ROOT / "rpa"))
    PACKAGE = "src.engine"
else:
    sys.path.insert(0, str(ROOT / "engine" / "src"))
    PACKAGE = "graphwright.engine"


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys that the normal YAML loader would overwrite."""


def unique_mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def main() -> int:
    engine = importlib.import_module(PACKAGE)
    failures: list[str] = []
    examples = sorted((DOCS / "examples").glob("*.yaml"))
    snippets = links = 0
    for path in examples:
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
            workflow = engine.Workflow.model_validate(data)
            if workflow.workflow != path.stem:
                raise ValueError("workflow must match example filename")
            engine.compile_workflow(workflow)
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")

    for path in sorted(DOCS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for language, block in re.findall(r"^```(yaml|json)\s*\n(.*?)^```", text, re.M | re.S):
            snippets += 1
            try:
                if language == "yaml":
                    yaml.load(block, Loader=UniqueLoader)
                else:
                    json.loads(block)
            except Exception as exc:
                failures.append(f"{path.name}: invalid {language} snippet: {exc}")
        prose = re.sub(r"^```[^\n]*\n.*?^```", "", text, flags=re.M | re.S)
        for destination in re.findall(r"\[[^\]]+\]\(([^)]+)\)", prose):
            if re.match(r"^[a-zA-Z][\w+.-]*:", destination) or destination.startswith("#"):
                continue
            target = unquote(destination.split("#", 1)[0])
            if target:
                links += 1
                if not (path.parent / target).exists():
                    failures.append(f"{path.name}: missing relative link {destination}")

    for failure in failures:
        print(f"FAIL {failure}")
    print(f"{len(examples)} graphs compiled; {snippets} snippets parsed; {links} file links checked; "
          f"{len(failures)} failure(s). No live services were called.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
