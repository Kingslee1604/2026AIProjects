import pytest

from tools.license_check import (
    Package,
    Scope,
    evaluate,
    license_allowed,
    load_policy,
    python_license,
)

POLICY = load_policy()


def pkg(license_: str | None, scope: Scope = "shipped", name: str = "somepkg") -> Package:
    return Package("pypi", name, "1.0.0", license_, scope)


@pytest.mark.parametrize("license_", ["MIT", "Apache-2.0", "BSD-3-Clause", "PostgreSQL", "MPL-2.0"])
def test_permissive_licenses_pass_when_shipped(license_: str) -> None:
    assert evaluate([pkg(license_)], POLICY) == []


@pytest.mark.parametrize(
    "license_", ["GPL-3.0-only", "GPL-2.0-or-later", "AGPL-3.0-only", "LGPL-3.0-only", "SSPL-1.0"]
)
def test_copyleft_and_source_available_licenses_fail(license_: str) -> None:
    violations = evaluate([pkg(license_)], POLICY)
    assert len(violations) == 1
    assert "not allowed" in violations[0].problem


def test_gpl_dev_tool_still_fails_because_it_is_not_in_dev_only_list() -> None:
    assert len(evaluate([pkg("GPL-3.0-only", scope="dev")], POLICY)) == 1


def test_dev_only_license_passes_for_dev_but_fails_when_shipped() -> None:
    assert evaluate([pkg("EPL-2.0", scope="dev")], POLICY) == []
    violations = evaluate([pkg("EPL-2.0", scope="shipped")], POLICY)
    assert len(violations) == 1
    assert "dev tools only" in violations[0].problem


def test_unknown_license_fails() -> None:
    violations = evaluate([pkg(None)], POLICY)
    assert violations[0].problem.startswith("license unknown")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT OR GPL-3.0-only", True),
        ("MIT AND GPL-3.0-only", False),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", True),
        ("(GPL-2.0-only OR GPL-3.0-only) AND MIT", False),
        ("Apache-2.0 WITH LLVM-exception", True),
    ],
)
def test_spdx_expressions(expression: str, expected: bool) -> None:
    assert license_allowed(expression, POLICY.allowed) is expected


def test_malformed_expression_is_a_violation_not_a_crash() -> None:
    violations = evaluate([pkg("(MIT OR")], POLICY)
    assert len(violations) == 1


def test_exception_overrides_missing_metadata_for_dev_scope_only() -> None:
    khroma_dev = Package("npm", "khroma", "2.1.0", None, "dev")
    khroma_shipped = Package("npm", "khroma", "2.1.0", None, "shipped")
    assert evaluate([khroma_dev], POLICY) == []
    assert "dev-only" in evaluate([khroma_shipped], POLICY)[0].problem


def test_workspace_packages_are_skipped() -> None:
    assert evaluate([pkg(None, name="agentforge")], POLICY) == []


@pytest.mark.parametrize(
    ("expression", "field", "classifiers", "expected"),
    [
        ("Apache-2.0 OR BSD-2-Clause", None, [], "Apache-2.0 OR BSD-2-Clause"),
        (None, "MIT License", [], "MIT"),
        (None, "BSD-3-Clause", [], "BSD-3-Clause"),
        (None, None, ["MIT License", "Apache Software License"], "Apache-2.0 OR MIT"),
        (None, "Some long custom license text\nwith many lines", [], None),
        (None, None, [], None),
    ],
)
def test_python_license_derivation(
    expression: str | None, field: str | None, classifiers: list[str], expected: str | None
) -> None:
    assert python_license(expression, field, classifiers) == expected
