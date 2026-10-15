"""Fail the build when a dependency's license is outside licenses/policy.toml.

Usage (from agentforge/): uv run --frozen python -m tools.license_check

Python packages come from the active (uv) environment; "shipped" means a runtime dependency
of the workspace (uv export --no-dev), everything else installed is "dev". npm packages come
from `pnpm licenses list`; "shipped" means it also appears with --prod.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path
from typing import Literal, cast

Scope = Literal["shipped", "dev"]

POLICY_PATH = Path(__file__).resolve().parent.parent / "licenses" / "policy.toml"
WORKSPACE_PACKAGES = {"agentforge", "agentforge-workspace"}

# Trove classifiers (last segment) → SPDX, for packages without License-Expression metadata.
CLASSIFIER_TO_SPDX = {
    "MIT License": "MIT",
    "MIT No Attribution License (MIT-0)": "MIT-0",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Affero General Public License v3": "AGPL-3.0-only",
}
# Common non-SPDX spellings found in the free-text License field.
ALIASES = {
    "mit license": "MIT",
    "the mit license": "MIT",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd 2-clause license": "BSD-2-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "psfl": "PSF-2.0",  # e.g. defusedxml (its LICENSE file is the PSF License Version 2)
}
SPDX_ID = re.compile(r"^[A-Za-z0-9.+-]+$")


@dataclass(frozen=True)
class Package:
    ecosystem: Literal["pypi", "npm"]
    name: str
    version: str
    license: str | None
    scope: Scope


@dataclass(frozen=True)
class PolicyException:
    ecosystem: str
    name: str
    license: str
    scope: Scope | Literal["any"]
    reason: str


@dataclass(frozen=True)
class Policy:
    allowed: frozenset[str]
    dev_only: frozenset[str]
    exceptions: dict[tuple[str, str], PolicyException]


@dataclass(frozen=True)
class Violation:
    package: Package
    problem: str


def load_policy(path: Path = POLICY_PATH) -> Policy:
    data = tomllib.loads(path.read_text())
    exceptions = {
        (e["ecosystem"], e["name"].lower()): PolicyException(
            e["ecosystem"], e["name"].lower(), e["license"], e.get("scope", "any"), e["reason"]
        )
        for e in data.get("exception", [])
    }
    return Policy(
        allowed=frozenset(data["allowed"]["spdx"]),
        dev_only=frozenset(data.get("dev_only", {}).get("spdx", [])),
        exceptions=exceptions,
    )


def _tokens(expression: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expression)


def license_allowed(expression: str, permitted: frozenset[str]) -> bool:
    """Evaluate an SPDX expression (AND, OR, WITH, parentheses) against permitted IDs."""
    tokens = _tokens(expression)
    pos = 0

    def parse_or() -> bool:
        nonlocal pos
        result = parse_and()
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            right = parse_and()
            result = result or right
        return result

    def parse_and() -> bool:
        nonlocal pos
        result = parse_atom()
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            right = parse_atom()
            result = result and right
        return result

    def parse_atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError(f"unexpected end of license expression: {expression!r}")
        symbol = tokens[pos]
        pos += 1
        if symbol == "(":
            value = parse_or()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ValueError(f"unbalanced parentheses in {expression!r}")
            pos += 1
            return value
        if pos < len(tokens) and tokens[pos].upper() == "WITH":
            pos += 2  # "X WITH some-exception": the exception only adds permissions
        return symbol.removesuffix("+") in permitted or symbol in permitted

    value = parse_or()
    if pos != len(tokens):
        raise ValueError(f"unparsed tokens in license expression: {expression!r}")
    return value


def evaluate(packages: list[Package], policy: Policy) -> list[Violation]:
    violations: list[Violation] = []
    for package in packages:
        if package.ecosystem == "pypi" and package.name.lower() in WORKSPACE_PACKAGES:
            continue
        expression = package.license
        exception = policy.exceptions.get((package.ecosystem, package.name.lower()))
        if exception is not None:
            # An exception approves exactly one verified license for one package. Missing
            # metadata is accepted (that is often why the exception exists); a different
            # published license means the package changed and must be re-reviewed.
            if exception.scope == "dev" and package.scope != "dev":
                violations.append(
                    Violation(package, f"exception is dev-only but package is {package.scope}")
                )
            elif expression and expression != exception.license:
                violations.append(
                    Violation(
                        package,
                        f"license changed from approved {exception.license!r} to {expression!r}",
                    )
                )
            continue
        if not expression:
            violations.append(Violation(package, "license unknown (no usable metadata)"))
            continue
        permitted = policy.allowed | policy.dev_only if package.scope == "dev" else policy.allowed
        try:
            ok = license_allowed(expression, permitted)
        except ValueError as error:
            violations.append(Violation(package, str(error)))
            continue
        if not ok:
            hint = (
                " (allowed for dev tools only)"
                if license_allowed(expression, policy.allowed | policy.dev_only)
                else ""
            )
            violations.append(
                Violation(package, f"license {expression!r} not allowed for {package.scope}{hint}")
            )
    return violations


def python_license(
    metadata_expression: str | None, license_field: str | None, classifiers: list[str]
) -> str | None:
    if metadata_expression:
        return metadata_expression.strip()
    first_line = (license_field or "").strip().splitlines()[0] if license_field else ""
    if first_line:
        alias = ALIASES.get(first_line.lower())
        if alias:
            return alias
        if SPDX_ID.match(first_line):
            return first_line
    mapped = [CLASSIFIER_TO_SPDX[c] for c in classifiers if c in CLASSIFIER_TO_SPDX]
    return " OR ".join(sorted(set(mapped))) or None


def _run(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def collect_python() -> list[Package]:
    exported = _run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--no-emit-workspace",
            "--format",
            "requirements-txt",
        ]
    )
    shipped = {
        line.split("==")[0].strip().lower()
        for line in exported.splitlines()
        if line and not line.startswith(("#", " ", "-"))
    }
    packages: list[Package] = []
    for dist in distributions():
        meta = dist.metadata
        name = meta["Name"]
        classifiers = [
            c.split(" :: ")[-1]
            for c in (meta.get_all("Classifier") or [])
            if c.startswith("License ::")
        ]
        packages.append(
            Package(
                "pypi",
                name,
                dist.version,
                python_license(meta.get("License-Expression"), meta.get("License"), classifiers),
                "shipped" if name.lower() in shipped else "dev",
            )
        )
    return packages


def collect_npm() -> list[Package]:
    def listing(prod: bool) -> dict[str, list[dict[str, object]]]:
        # -r: every workspace project (the web app), not only the workspace root.
        args = ["pnpm", "licenses", "list", "--json", "-r"] + (["--prod"] if prod else [])
        output = _run(args).strip()
        return json.loads(output) if output else {}

    shipped = {str(p["name"]) for pkgs in listing(prod=True).values() for p in pkgs}
    packages: list[Package] = []
    for license_id, entries in listing(prod=False).items():
        for entry in entries:
            name = str(entry["name"])
            versions = entry.get("versions")
            version = str(cast(list[object], versions)[0]) if isinstance(versions, list) else ""
            packages.append(
                Package(
                    "npm",
                    name,
                    version,
                    None if license_id == "Unknown" else license_id,
                    "shipped" if name in shipped else "dev",
                )
            )
    return packages


def main() -> int:
    policy = load_policy()
    packages = collect_python() + collect_npm()
    violations = evaluate(packages, policy)
    shipped = sum(p.scope == "shipped" for p in packages)
    print(
        f"license check: {len(packages)} packages ({shipped} shipped, "
        f"{len(packages) - shipped} dev), {len(violations)} violation(s)"
    )
    for v in violations:
        p = v.package
        print(f"  FAIL {p.ecosystem}:{p.name}@{p.version} [{p.scope}] {v.problem}")
    if violations:
        print("Fix the dependency, or record an approved exception in licenses/policy.toml.")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
