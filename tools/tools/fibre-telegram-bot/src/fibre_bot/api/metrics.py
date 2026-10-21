"""Typed access to the Prometheus text served by /metrics.

An absent series means "no data", so every accessor returns None instead of 0.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from prometheus_client.parser import text_string_to_metric_families

Labels = dict[str, str]

PERIODS_WITH_LABEL = ("24h", "7d", "30d")


@dataclass
class MetricsSnapshot:
    samples: dict[str, list[tuple[Labels, float]]] = field(default_factory=dict)

    @classmethod
    def parse(cls, text: str) -> MetricsSnapshot:
        samples: dict[str, list[tuple[Labels, float]]] = defaultdict(list)
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                value = float(sample.value)
                if math.isnan(value):
                    continue
                samples[sample.name].append((dict(sample.labels), value))
        return cls(samples=dict(samples))

    # Generic accessors -------------------------------------------------------------------

    def get(self, name: str, **match: str) -> float | None:
        """First sample of `name` whose labels include every key in `match`."""
        for labels, value in self.samples.get(name, ()):
            if all(labels.get(k) == v for k, v in match.items()):
                return value
        return None

    def by_label(self, name: str, label: str, **match: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for labels, value in self.samples.get(name, ()):
            if label in labels and all(labels.get(k) == v for k, v in match.items()):
                out[labels[label]] = value
        return out

    def label_value(self, name: str, label: str, **match: str) -> str | None:
        for labels, _ in self.samples.get(name, ()):
            if all(labels.get(k) == v for k, v in match.items()):
                return labels.get(label)
        return None

    # Network -----------------------------------------------------------------------------

    @property
    def generated_at(self) -> float | None:
        return self.get("fibre_data_generated_timestamp_seconds")

    @property
    def probes_up(self) -> float | None:
        values = self.by_label("fibre_probe_up", "region")
        return sum(values.values()) if values else None

    # Validators --------------------------------------------------------------------------

    def validators(self) -> dict[str, dict[str, str]]:
        """valcons -> {moniker, operator_address} from fibre_validator_info."""
        out: dict[str, dict[str, str]] = {}
        for labels, _ in self.samples.get("fibre_validator_info", ()):
            valcons = labels.get("valcons")
            if valcons:
                out[valcons] = {
                    "moniker": labels.get("moniker", ""),
                    "operator_address": labels.get("operator_address", ""),
                }
        return out

    def validator(self, valcons: str) -> ValidatorView:
        return ValidatorView(self, valcons)

    def find_valcons(self, ident: str) -> str | None:
        """Resolve a valcons, valoper or exact moniker (case-insensitive) locally."""
        ident = ident.strip()
        known = self.validators()
        if ident in known:
            return ident
        lowered = ident.lower()
        matches = [
            v
            for v, info in known.items()
            if info["operator_address"] == ident or info["moniker"].lower() == lowered
        ]
        return matches[0] if len(matches) == 1 else None


@dataclass
class ValidatorView:
    snapshot: MetricsSnapshot
    valcons: str

    def get(self, name: str, period: str | None = None, **match: str) -> float | None:
        if period is not None:
            match["period"] = period
        return self.snapshot.get(name, valcons=self.valcons, **match)

    def by_region(self, name: str) -> dict[str, float]:
        return self.snapshot.by_label(name, "region", valcons=self.valcons)

    @property
    def exists(self) -> bool:
        return self.snapshot.get("fibre_validator_info", valcons=self.valcons) is not None

    @property
    def moniker(self) -> str | None:
        return self.snapshot.label_value("fibre_validator_info", "moniker", valcons=self.valcons)
