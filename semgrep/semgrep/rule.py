import json
from collections import namedtuple
from typing import Any
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional

from semgrep.equivalences import Equivalence
from semgrep.rule_lang import DUMMY_SPAN
from semgrep.rule_lang import RuleLangError
from semgrep.rule_lang import Span
from semgrep.semgrep_types import ALLOWED_GLOB_TYPES
from semgrep.semgrep_types import BooleanRuleExpression
from semgrep.semgrep_types import InvalidRuleSchema
from semgrep.semgrep_types import operator_for_pattern_name
from semgrep.semgrep_types import OPERATORS
from semgrep.semgrep_types import pattern_names_for_operator
from semgrep.semgrep_types import pattern_names_for_operators
from semgrep.semgrep_types import PatternId
from semgrep.semgrep_types import RuleGlobs
from semgrep.semgrep_types import YAML_VALID_TOP_LEVEL_OPERATORS


class Rule:
    def __init__(self, raw: Dict[str, Any]) -> None:
        self._raw = raw
        self._expression = self._build_boolean_expression(raw)
        self._globs = self._build_globs(raw)

    def _parse_boolean_expression(
        self,
        rule_patterns: List[Dict[str, Any]],
        parent: Dict[str, Any],
        pattern_id: int = 0,
        prefix: str = "",
    ) -> Iterator[BooleanRuleExpression]:
        """
        Move through the expression from the YML, yielding tuples of (operator, unique-id-for-pattern, pattern)
        """
        if not isinstance(rule_patterns, list):
            if isinstance(rule_patterns, dict):
                span = Span.from_dict(rule_patterns, before_context=1)
            else:
                span = Span.from_dict(parent)

            err = RuleLangError(
                short_msg="invalid type for `patterns`",
                long_msg=f"invalid type for `patterns` (expected list, found {type(rule_patterns).__name__})",
                level="error",
                help="perhaps your YAML is missing a `-`?",
                spans=[span or DUMMY_SPAN],
            )
            raise InvalidRuleSchema(err.emit())
        for pattern in rule_patterns:
            if not isinstance(pattern, dict):
                raise InvalidRuleSchema(
                    f"invalid type for pattern {pattern}: {type(pattern)} is not a dict"
                )
            span = Span.from_dict(pattern)
            for boolean_operator, pattern_text in pattern.items():
                if boolean_operator.startswith("__"):
                    continue
                operator = operator_for_pattern_name(boolean_operator)
                if isinstance(pattern_text, list):
                    sub_expression = self._parse_boolean_expression(
                        pattern_text,
                        parent=pattern,
                        pattern_id=0,
                        prefix=f"{prefix}.{pattern_id}",
                    )
                    yield BooleanRuleExpression(
                        operator, None, list(sub_expression), None, span=span
                    )
                elif isinstance(pattern_text, str):
                    yield BooleanRuleExpression(
                        operator,
                        PatternId(f"{prefix}.{pattern_id}"),
                        None,
                        pattern_text,
                        span=span,
                    )
                    pattern_id += 1
                else:
                    raise InvalidRuleSchema(
                        f"invalid type for pattern {pattern}: {type(pattern_text)}"
                    )

    def _build_boolean_expression(
        self, rule_raw: Dict[str, Any]
    ) -> BooleanRuleExpression:
        """
        Build a boolean expression from the yml lines in the rule

        """
        span = Span.from_dict(rule_raw)
        for pattern_name in pattern_names_for_operator(OPERATORS.AND):
            pattern = rule_raw.get(pattern_name)
            if pattern:
                return BooleanRuleExpression(
                    OPERATORS.AND,
                    rule_raw["id"],
                    None,
                    rule_raw[pattern_name],
                    span=span,
                )

        for pattern_name in pattern_names_for_operator(OPERATORS.REGEX):
            pattern = rule_raw.get(pattern_name)
            if pattern:
                return BooleanRuleExpression(
                    OPERATORS.REGEX,
                    rule_raw["id"],
                    None,
                    rule_raw[pattern_name],
                    span=span,
                )

        for pattern_name in pattern_names_for_operator(OPERATORS.AND_ALL):
            patterns = rule_raw.get(pattern_name)
            if patterns:
                return BooleanRuleExpression(
                    OPERATORS.AND_ALL,
                    None,
                    list(self._parse_boolean_expression(patterns, parent=rule_raw)),
                    None,
                    span=span,
                )

        for pattern_name in pattern_names_for_operator(OPERATORS.AND_EITHER):
            patterns = rule_raw.get(pattern_name)
            if patterns:
                return BooleanRuleExpression(
                    OPERATORS.AND_EITHER,
                    None,
                    list(self._parse_boolean_expression(patterns, parent=rule_raw)),
                    None,
                    span=span,
                )

        valid_top_level_keys = list(YAML_VALID_TOP_LEVEL_OPERATORS)
        raise InvalidRuleSchema(
            f"missing a pattern type in rule, expected one of {pattern_names_for_operators(valid_top_level_keys)}"
        )

    @staticmethod
    def _build_globs(rule_raw: Dict[str, Any]) -> RuleGlobs:  # type: ignore
        """
        Return a list of globs to be included and excluded for the given `paths:` rules.

        Glob conversion works as follows

        - path: tests/*.py -> tests/*.py
        - directory: tests -> tests/**
        - filename: *.js -> **/*.js
        """
        globs = RuleGlobs(set(), set())

        paths_raw = rule_raw.get("paths", {})
        if not isinstance(paths_raw, dict):
            raise InvalidRuleSchema(
                f"the `paths:` targeting rules must be an object with at least one of {ALLOWED_GLOB_TYPES}"
            )

        for rule_type, rule in rule_raw.get("paths", {}).items():
            if rule_type.startswith("__"):
                continue
            if rule_type not in ALLOWED_GLOB_TYPES:
                raise InvalidRuleSchema(
                    f"the `paths:` targeting rules must each be one of {ALLOWED_GLOB_TYPES}"
                )

            glob_set = globs.exclude if rule_type == "exclude" else globs.include
            rule_values = [rule] if isinstance(rule, str) else rule

            for rule_value in rule_values:
                glob_set.add(rule_value)

        return globs

    @property
    def id(self) -> str:
        return str(self._raw["id"])

    @property
    def message(self) -> str:
        return str(self._raw["message"])

    @property
    def metadata(self) -> Dict[str, Any]:  # type: ignore
        return self._raw.get("metadata", {})

    @property
    def severity(self) -> str:
        return str(self._raw["severity"])

    @property
    def sarif_severity(self) -> str:
        """
        SARIF v2.1.0-compliant severity string.

        See https://github.com/oasis-tcs/sarif-spec/blob/a6473580/Schemata/sarif-schema-2.1.0.json#L1566
        """
        mapping = {"INFO": "note", "ERROR": "error", "WARNING": "warning"}
        return mapping[self.severity]

    @property
    def sarif_tags(self) -> Iterator[str]:
        """
        Tags to display on SARIF-compliant UIs, such as GitHub security scans.
        """
        if "cwe" in self.metadata:
            yield "cwe"
        if "owasp" in self.metadata:
            yield "owasp"

    @property
    def languages(self) -> List[str]:
        languages: List[str] = self._raw["languages"]
        return languages

    @property
    def raw(self) -> Dict[str, Any]:  # type: ignore
        return self._raw

    @property
    def expression(self) -> BooleanRuleExpression:
        return self._expression

    @property
    def globs(self) -> RuleGlobs:
        return self._globs

    @property
    def fix(self) -> Optional[str]:
        return self._raw.get("fix")

    @property
    def equivalences(self) -> List[Equivalence]:
        # Use 'i' to make equivalence id's unique
        return [
            Equivalence(f"{self.id}-{i}", eq["equivalence"], self.languages)
            for i, eq in enumerate(self._raw.get(OPERATORS.EQUIVALENCES, []))
        ]

    @classmethod
    def from_json(cls, rule_json: Dict[str, Any]) -> "Rule":  # type: ignore
        return cls(rule_json)

    def to_json(self) -> Dict[str, Any]:
        return self._raw

    def to_sarif(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.id,
            "shortDescription": {"text": self.message},
            "fullDescription": {"text": self.message},
            "defaultConfiguration": {"level": self.sarif_severity},
            "properties": {"precision": "very-high", "tags": list(self.sarif_tags)},
        }

    def __repr__(self) -> str:
        return json.dumps(self.to_json())
