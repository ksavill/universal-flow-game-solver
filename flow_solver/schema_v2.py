from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, TYPE_CHECKING

from .graph import Graph, Node

if TYPE_CHECKING:
    from .puzzle import Puzzle


FORMAT_NAME = "flow-solver-puzzle"
SCHEMA_VERSION = 2

ADJACENCY_KINDS = frozenset({"local", "seam", "warp", "custom"})
ADJACENCY_STATES = frozenset({"open", "blocked"})
COVERAGE_MODES = frozenset({"all-cells", "optional"})
MULTI_CHANNEL_COLOR_POLICIES = frozenset({"distinct", "allow"})

Position = Tuple[float, float, float]


class SchemaV2Error(ValueError):
    """Raised when a schema-v2 document is malformed or unsupported."""


@dataclass(frozen=True)
class PortSpec:
    kind: str = "port"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CellSpec:
    kind: str = "cell"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelSpec:
    cell: str
    ports: Dict[str, PortSpec]
    kind: str = "cell"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdjacencyEndpoint:
    channel: str
    port: str


@dataclass(frozen=True)
class AdjacencySpec:
    id: str
    a: AdjacencyEndpoint
    b: AdjacencyEndpoint
    kind: str = "local"
    state: str = "open"
    group: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TopologySpec:
    cells: Dict[str, CellSpec]
    channels: Dict[str, ChannelSpec]
    adjacencies: Tuple[AdjacencySpec, ...]
    template: Optional[TemplateSpec] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TerminalSpec:
    endpoints: Tuple[str, str]
    color: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CellCoverageOverride:
    min_used_channels: Optional[int] = None
    max_used_channels: Optional[int] = None


@dataclass(frozen=True)
class CoverageSpec:
    mode: str = "all-cells"
    overrides: Dict[str, CellCoverageOverride] = field(default_factory=dict)


@dataclass(frozen=True)
class PathRulesSpec:
    endpoint_degree: int = 1
    internal_degree: int = 2
    connected: bool = True


@dataclass(frozen=True)
class RulesSpec:
    coverage: CoverageSpec = field(default_factory=CoverageSpec)
    paths: PathRulesSpec = field(default_factory=PathRulesSpec)
    multi_channel_cell_color_policy: str = "distinct"


@dataclass(frozen=True)
class CellDisplaySpec:
    position: Optional[Position] = None
    polygon: Tuple[Position, ...] = ()
    layer: Optional[str] = None
    face: Optional[str] = None
    z_index: Optional[float] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelDisplaySpec:
    position: Optional[Position] = None
    layer: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortDisplaySpec:
    position: Optional[Position] = None
    normal: Optional[Position] = None
    layer: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdjacencyDisplaySpec:
    points: Tuple[Position, ...] = ()
    layer: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DisplaySpec:
    dimension: int = 2
    cells: Dict[str, CellDisplaySpec] = field(default_factory=dict)
    channels: Dict[str, ChannelDisplaySpec] = field(default_factory=dict)
    ports: Dict[str, Dict[str, PortDisplaySpec]] = field(default_factory=dict)
    adjacencies: Dict[str, AdjacencyDisplaySpec] = field(default_factory=dict)
    layers: Tuple[str, ...] = ()
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogPackSpec:
    id: Optional[str] = None
    name: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogLevelSpec:
    id: Optional[str] = None
    number: Optional[int] = None
    name: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DisplaySizeSpec:
    label: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    unit: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogSpec:
    app: Optional[str] = None
    variant: Optional[str] = None
    pack: Optional[CatalogPackSpec] = None
    level: Optional[CatalogLevelSpec] = None
    mode: Optional[str] = None
    display_size: Optional[DisplaySizeSpec] = None
    mechanics: Tuple[str, ...] = ()
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PuzzleSpec:
    topology: TopologySpec
    terminals: Dict[str, TerminalSpec]
    rules: RulesSpec = field(default_factory=RulesSpec)
    display: DisplaySpec = field(default_factory=DisplaySpec)
    catalog: CatalogSpec = field(default_factory=CatalogSpec)
    meta: Dict[str, Any] = field(default_factory=dict)
    extensions: Dict[str, Any] = field(default_factory=dict)
    format: str = FORMAT_NAME
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PuzzleSpec":
        return parse_v2_dict(raw)

    @classmethod
    def from_json(cls, text: str) -> "PuzzleSpec":
        return parse_v2_json(text)

    def to_dict(self) -> Dict[str, Any]:
        return spec_to_dict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return spec_to_json(self, indent=indent)

    def compile(self) -> "Puzzle":
        return compile_puzzle_spec(self)


def has_v2_marker(raw: Any) -> bool:
    return isinstance(raw, Mapping) and ("format" in raw or "schema_version" in raw)


def _error(path: str, message: str) -> SchemaV2Error:
    return SchemaV2Error(f"{path}: {message}")


def _child_path(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{key!r}]"


def _expect_object(raw: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise _error(path, "must be an object")
    for key in raw:
        if not isinstance(key, str):
            raise _error(path, "all object keys must be strings")
    return raw


def _expect_list(raw: Any, path: str) -> Sequence[Any]:
    if not isinstance(raw, list):
        raise _error(path, "must be a list")
    return raw


def _check_keys(
    raw: Mapping[str, Any],
    path: str,
    *,
    required: Sequence[str] = (),
    optional: Sequence[str] = (),
) -> None:
    missing = sorted(set(required) - set(raw))
    if missing:
        raise _error(path, f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(raw) - set(required) - set(optional))
    if unknown:
        raise _error(path, f"unknown field(s): {', '.join(unknown)}")


def _expect_string(raw: Any, path: str, *, nonempty: bool = True) -> str:
    if not isinstance(raw, str):
        raise _error(path, "must be a string")
    if nonempty and not raw.strip():
        raise _error(path, "must not be empty")
    return raw


def _optional_string(raw: Any, path: str) -> Optional[str]:
    if raw is None:
        return None
    return _expect_string(raw, path)


def _expect_int(raw: Any, path: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _error(path, "must be an integer")
    if minimum is not None and raw < minimum:
        raise _error(path, f"must be at least {minimum}")
    return raw


def _expect_number(raw: Any, path: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _error(path, "must be a number")
    value = float(raw)
    if not math.isfinite(value):
        raise _error(path, "must be finite")
    return value


def _expect_bool(raw: Any, path: str) -> bool:
    if not isinstance(raw, bool):
        raise _error(path, "must be a boolean")
    return raw


def _validate_json_value(raw: Any, path: str) -> None:
    if raw is None or isinstance(raw, (str, bool, int)):
        return
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise _error(path, "must not contain NaN or infinity")
        return
    if isinstance(raw, list):
        for idx, value in enumerate(raw):
            _validate_json_value(value, f"{path}[{idx}]")
        return
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if not isinstance(key, str):
                raise _error(path, "all object keys must be strings")
            _validate_json_value(value, _child_path(path, key))
        return
    raise _error(path, f"contains a non-JSON value of type {type(raw).__name__}")


def _json_object(raw: Any, path: str) -> Dict[str, Any]:
    obj = _expect_object(raw, path)
    _validate_json_value(obj, path)
    return copy.deepcopy(dict(obj))


def _parse_position(raw: Any, path: str) -> Position:
    values = _expect_list(raw, path)
    if len(values) not in {2, 3}:
        raise _error(path, "must contain 2 or 3 coordinates")
    coords = [_expect_number(value, f"{path}[{idx}]") for idx, value in enumerate(values)]
    if len(coords) == 2:
        coords.append(0.0)
    return (coords[0], coords[1], coords[2])


def _parse_positions(raw: Any, path: str) -> Tuple[Position, ...]:
    values = _expect_list(raw, path)
    return tuple(_parse_position(value, f"{path}[{idx}]") for idx, value in enumerate(values))


def _parse_port(raw: Any, path: str) -> PortSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("kind", "data"))
    kind = _expect_string(obj.get("kind", "port"), f"{path}.kind")
    data = _json_object(obj.get("data", {}), f"{path}.data")
    return PortSpec(kind=kind, data=data)


def _parse_cell(raw: Any, path: str) -> CellSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("kind", "data"))
    kind = _expect_string(obj.get("kind", "cell"), f"{path}.kind")
    data = _json_object(obj.get("data", {}), f"{path}.data")
    return CellSpec(kind=kind, data=data)


def _parse_channel(raw: Any, path: str) -> ChannelSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, required=("cell", "ports"), optional=("kind", "data"))
    cell = _expect_string(obj["cell"], f"{path}.cell")
    kind = _expect_string(obj.get("kind", "cell"), f"{path}.kind")
    ports_obj = _expect_object(obj["ports"], f"{path}.ports")
    ports: Dict[str, PortSpec] = {}
    for port_id in sorted(ports_obj):
        _expect_string(port_id, f"{path}.ports key")
        ports[port_id] = _parse_port(ports_obj[port_id], _child_path(f"{path}.ports", port_id))
    data = _json_object(obj.get("data", {}), f"{path}.data")
    return ChannelSpec(cell=cell, ports=ports, kind=kind, data=data)


def _parse_endpoint(raw: Any, path: str) -> AdjacencyEndpoint:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, required=("channel", "port"))
    return AdjacencyEndpoint(
        channel=_expect_string(obj["channel"], f"{path}.channel"),
        port=_expect_string(obj["port"], f"{path}.port"),
    )


def _parse_adjacency(raw: Any, path: str) -> AdjacencySpec:
    obj = _expect_object(raw, path)
    _check_keys(
        obj,
        path,
        required=("id", "a", "b"),
        optional=("kind", "state", "group", "data"),
    )
    kind = _expect_string(obj.get("kind", "local"), f"{path}.kind")
    if kind not in ADJACENCY_KINDS:
        raise _error(f"{path}.kind", f"must be one of: {', '.join(sorted(ADJACENCY_KINDS))}")
    state = _expect_string(obj.get("state", "open"), f"{path}.state")
    if state not in ADJACENCY_STATES:
        raise _error(f"{path}.state", f"must be one of: {', '.join(sorted(ADJACENCY_STATES))}")
    return AdjacencySpec(
        id=_expect_string(obj["id"], f"{path}.id"),
        a=_parse_endpoint(obj["a"], f"{path}.a"),
        b=_parse_endpoint(obj["b"], f"{path}.b"),
        kind=kind,
        state=state,
        group=_optional_string(obj.get("group"), f"{path}.group"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_template(raw: Any, path: str) -> TemplateSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, required=("id",), optional=("parameters",))
    return TemplateSpec(
        id=_expect_string(obj["id"], f"{path}.id"),
        parameters=_json_object(obj.get("parameters", {}), f"{path}.parameters"),
    )


def _parse_topology(raw: Any, path: str) -> TopologySpec:
    obj = _expect_object(raw, path)
    _check_keys(
        obj,
        path,
        required=("cells", "channels", "adjacencies"),
        optional=("template", "data"),
    )
    cells_obj = _expect_object(obj["cells"], f"{path}.cells")
    if not cells_obj:
        raise _error(f"{path}.cells", "must contain at least one cell")
    cells: Dict[str, CellSpec] = {}
    for cell_id in sorted(cells_obj):
        _expect_string(cell_id, f"{path}.cells key")
        cells[cell_id] = _parse_cell(cells_obj[cell_id], _child_path(f"{path}.cells", cell_id))

    channels_obj = _expect_object(obj["channels"], f"{path}.channels")
    channels: Dict[str, ChannelSpec] = {}
    for channel_id in sorted(channels_obj):
        _expect_string(channel_id, f"{path}.channels key")
        channels[channel_id] = _parse_channel(
            channels_obj[channel_id], _child_path(f"{path}.channels", channel_id)
        )

    adjacency_list = _expect_list(obj["adjacencies"], f"{path}.adjacencies")
    adjacencies = tuple(
        sorted(
            (
                _parse_adjacency(item, f"{path}.adjacencies[{idx}]")
                for idx, item in enumerate(adjacency_list)
            ),
            key=lambda adjacency: adjacency.id,
        )
    )
    template = _parse_template(obj["template"], f"{path}.template") if "template" in obj else None
    return TopologySpec(
        cells=cells,
        channels=channels,
        adjacencies=adjacencies,
        template=template,
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_terminal(raw: Any, path: str) -> TerminalSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, required=("endpoints",), optional=("color", "data"))
    endpoints_raw = _expect_list(obj["endpoints"], f"{path}.endpoints")
    if len(endpoints_raw) != 2:
        raise _error(f"{path}.endpoints", "must contain exactly two channel ids")
    endpoints = (
        _expect_string(endpoints_raw[0], f"{path}.endpoints[0]"),
        _expect_string(endpoints_raw[1], f"{path}.endpoints[1]"),
    )
    return TerminalSpec(
        endpoints=endpoints,
        color=_optional_string(obj.get("color"), f"{path}.color"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_coverage_override(raw: Any, path: str) -> CellCoverageOverride:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("min_used_channels", "max_used_channels"))
    minimum = (
        _expect_int(obj["min_used_channels"], f"{path}.min_used_channels", minimum=0)
        if "min_used_channels" in obj
        else None
    )
    maximum = (
        _expect_int(obj["max_used_channels"], f"{path}.max_used_channels", minimum=0)
        if "max_used_channels" in obj
        else None
    )
    if minimum is not None and maximum is not None and maximum < minimum:
        raise _error(path, "max_used_channels must be greater than or equal to min_used_channels")
    return CellCoverageOverride(min_used_channels=minimum, max_used_channels=maximum)


def _parse_coverage(raw: Any, path: str) -> CoverageSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("mode", "overrides"))
    mode = _expect_string(obj.get("mode", "all-cells"), f"{path}.mode")
    if mode not in COVERAGE_MODES:
        raise _error(f"{path}.mode", f"must be one of: {', '.join(sorted(COVERAGE_MODES))}")
    overrides_obj = _expect_object(obj.get("overrides", {}), f"{path}.overrides")
    overrides = {
        cell_id: _parse_coverage_override(value, _child_path(f"{path}.overrides", cell_id))
        for cell_id, value in sorted(overrides_obj.items())
    }
    return CoverageSpec(mode=mode, overrides=overrides)


def _parse_path_rules(raw: Any, path: str) -> PathRulesSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("endpoint_degree", "internal_degree", "connected"))
    return PathRulesSpec(
        endpoint_degree=_expect_int(obj.get("endpoint_degree", 1), f"{path}.endpoint_degree", minimum=0),
        internal_degree=_expect_int(obj.get("internal_degree", 2), f"{path}.internal_degree", minimum=0),
        connected=_expect_bool(obj.get("connected", True), f"{path}.connected"),
    )


def _parse_rules(raw: Any, path: str) -> RulesSpec:
    obj = _expect_object(raw, path)
    _check_keys(
        obj,
        path,
        optional=("coverage", "paths", "multi_channel_cell_color_policy"),
    )
    policy = _expect_string(
        obj.get("multi_channel_cell_color_policy", "distinct"),
        f"{path}.multi_channel_cell_color_policy",
    )
    if policy not in MULTI_CHANNEL_COLOR_POLICIES:
        raise _error(
            f"{path}.multi_channel_cell_color_policy",
            f"must be one of: {', '.join(sorted(MULTI_CHANNEL_COLOR_POLICIES))}",
        )
    return RulesSpec(
        coverage=_parse_coverage(obj.get("coverage", {}), f"{path}.coverage"),
        paths=_parse_path_rules(obj.get("paths", {}), f"{path}.paths"),
        multi_channel_cell_color_policy=policy,
    )


def _parse_cell_display(raw: Any, path: str) -> CellDisplaySpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("position", "polygon", "layer", "face", "z_index", "data"))
    return CellDisplaySpec(
        position=_parse_position(obj["position"], f"{path}.position") if "position" in obj else None,
        polygon=_parse_positions(obj.get("polygon", []), f"{path}.polygon"),
        layer=_optional_string(obj.get("layer"), f"{path}.layer"),
        face=_optional_string(obj.get("face"), f"{path}.face"),
        z_index=_expect_number(obj["z_index"], f"{path}.z_index") if "z_index" in obj else None,
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_channel_display(raw: Any, path: str) -> ChannelDisplaySpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("position", "layer", "data"))
    return ChannelDisplaySpec(
        position=_parse_position(obj["position"], f"{path}.position") if "position" in obj else None,
        layer=_optional_string(obj.get("layer"), f"{path}.layer"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_port_display(raw: Any, path: str) -> PortDisplaySpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("position", "normal", "layer", "data"))
    return PortDisplaySpec(
        position=_parse_position(obj["position"], f"{path}.position") if "position" in obj else None,
        normal=_parse_position(obj["normal"], f"{path}.normal") if "normal" in obj else None,
        layer=_optional_string(obj.get("layer"), f"{path}.layer"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_adjacency_display(raw: Any, path: str) -> AdjacencyDisplaySpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("points", "layer", "data"))
    return AdjacencyDisplaySpec(
        points=_parse_positions(obj.get("points", []), f"{path}.points"),
        layer=_optional_string(obj.get("layer"), f"{path}.layer"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_display(raw: Any, path: str) -> DisplaySpec:
    obj = _expect_object(raw, path)
    _check_keys(
        obj,
        path,
        optional=("dimension", "cells", "channels", "ports", "adjacencies", "layers", "data"),
    )
    dimension = _expect_int(obj.get("dimension", 2), f"{path}.dimension")
    if dimension not in {2, 3}:
        raise _error(f"{path}.dimension", "must be 2 or 3")

    cells_obj = _expect_object(obj.get("cells", {}), f"{path}.cells")
    cells = {
        key: _parse_cell_display(value, _child_path(f"{path}.cells", key))
        for key, value in sorted(cells_obj.items())
    }
    channels_obj = _expect_object(obj.get("channels", {}), f"{path}.channels")
    channels = {
        key: _parse_channel_display(value, _child_path(f"{path}.channels", key))
        for key, value in sorted(channels_obj.items())
    }

    ports_obj = _expect_object(obj.get("ports", {}), f"{path}.ports")
    ports: Dict[str, Dict[str, PortDisplaySpec]] = {}
    for channel_id, channel_ports_raw in sorted(ports_obj.items()):
        channel_ports_obj = _expect_object(channel_ports_raw, _child_path(f"{path}.ports", channel_id))
        ports[channel_id] = {
            port_id: _parse_port_display(
                port_raw,
                _child_path(_child_path(f"{path}.ports", channel_id), port_id),
            )
            for port_id, port_raw in sorted(channel_ports_obj.items())
        }

    adjacencies_obj = _expect_object(obj.get("adjacencies", {}), f"{path}.adjacencies")
    adjacencies = {
        key: _parse_adjacency_display(value, _child_path(f"{path}.adjacencies", key))
        for key, value in sorted(adjacencies_obj.items())
    }
    layers_raw = _expect_list(obj.get("layers", []), f"{path}.layers")
    layers = tuple(_expect_string(value, f"{path}.layers[{idx}]") for idx, value in enumerate(layers_raw))
    if len(set(layers)) != len(layers):
        raise _error(f"{path}.layers", "must not contain duplicates")

    return DisplaySpec(
        dimension=dimension,
        cells=cells,
        channels=channels,
        ports=ports,
        adjacencies=adjacencies,
        layers=layers,
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_catalog_pack(raw: Any, path: str) -> CatalogPackSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("id", "name", "data"))
    return CatalogPackSpec(
        id=_optional_string(obj.get("id"), f"{path}.id"),
        name=_optional_string(obj.get("name"), f"{path}.name"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_catalog_level(raw: Any, path: str) -> CatalogLevelSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("id", "number", "name", "data"))
    return CatalogLevelSpec(
        id=_optional_string(obj.get("id"), f"{path}.id"),
        number=_expect_int(obj["number"], f"{path}.number", minimum=0) if "number" in obj else None,
        name=_optional_string(obj.get("name"), f"{path}.name"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_display_size(raw: Any, path: str) -> DisplaySizeSpec:
    obj = _expect_object(raw, path)
    _check_keys(obj, path, optional=("label", "width", "height", "unit", "data"))
    return DisplaySizeSpec(
        label=_optional_string(obj.get("label"), f"{path}.label"),
        width=_expect_int(obj["width"], f"{path}.width", minimum=1) if "width" in obj else None,
        height=_expect_int(obj["height"], f"{path}.height", minimum=1) if "height" in obj else None,
        unit=_optional_string(obj.get("unit"), f"{path}.unit"),
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _parse_catalog(raw: Any, path: str) -> CatalogSpec:
    obj = _expect_object(raw, path)
    _check_keys(
        obj,
        path,
        optional=("app", "variant", "pack", "level", "mode", "display_size", "mechanics", "data"),
    )
    mechanics_raw = _expect_list(obj.get("mechanics", []), f"{path}.mechanics")
    mechanics = tuple(
        _expect_string(value, f"{path}.mechanics[{idx}]") for idx, value in enumerate(mechanics_raw)
    )
    if len(set(mechanics)) != len(mechanics):
        raise _error(f"{path}.mechanics", "must not contain duplicates")
    return CatalogSpec(
        app=_optional_string(obj.get("app"), f"{path}.app"),
        variant=_optional_string(obj.get("variant"), f"{path}.variant"),
        pack=_parse_catalog_pack(obj["pack"], f"{path}.pack") if "pack" in obj else None,
        level=_parse_catalog_level(obj["level"], f"{path}.level") if "level" in obj else None,
        mode=_optional_string(obj.get("mode"), f"{path}.mode"),
        display_size=_parse_display_size(obj["display_size"], f"{path}.display_size")
        if "display_size" in obj
        else None,
        mechanics=mechanics,
        data=_json_object(obj.get("data", {}), f"{path}.data"),
    )


def _validate_references(spec: PuzzleSpec) -> None:
    topology = spec.topology
    channels_by_cell: Dict[str, list[str]] = {cell_id: [] for cell_id in topology.cells}
    for channel_id, channel in topology.channels.items():
        if channel.cell not in topology.cells:
            raise _error(
                _child_path("$.topology.channels", channel_id) + ".cell",
                f"references unknown cell {channel.cell!r}",
            )
        channels_by_cell[channel.cell].append(channel_id)
    for cell_id, channel_ids in channels_by_cell.items():
        if not channel_ids:
            raise _error(
                _child_path("$.topology.cells", cell_id),
                "must own at least one channel",
            )

    adjacency_ids: set[str] = set()
    open_ports: set[tuple[str, str]] = set()
    open_channel_pairs: set[tuple[str, str]] = set()
    for adjacency in topology.adjacencies:
        adjacency_path = f"$.topology.adjacencies[{adjacency.id!r}]"
        if adjacency.id in adjacency_ids:
            raise _error(adjacency_path, "adjacency id is duplicated")
        adjacency_ids.add(adjacency.id)
        for side_name, endpoint in (("a", adjacency.a), ("b", adjacency.b)):
            if endpoint.channel not in topology.channels:
                raise _error(
                    f"{adjacency_path}.{side_name}.channel",
                    f"references unknown channel {endpoint.channel!r}",
                )
            if endpoint.port not in topology.channels[endpoint.channel].ports:
                raise _error(
                    f"{adjacency_path}.{side_name}.port",
                    f"references unknown port {endpoint.port!r} on channel {endpoint.channel!r}",
                )
        if adjacency.a.channel == adjacency.b.channel:
            raise _error(adjacency_path, "must connect two different channels")
        if adjacency.state == "open":
            for endpoint in (adjacency.a, adjacency.b):
                port_key = (endpoint.channel, endpoint.port)
                if port_key in open_ports:
                    raise _error(
                        adjacency_path,
                        f"open port {endpoint.channel!r}/{endpoint.port!r} is already connected",
                    )
                open_ports.add(port_key)
            channel_pair = tuple(sorted((adjacency.a.channel, adjacency.b.channel)))
            if channel_pair in open_channel_pairs:
                raise _error(
                    adjacency_path,
                    "parallel enabled adjacencies between the same channels are not supported yet",
                )
            open_channel_pairs.add(channel_pair)

    terminal_channels: Dict[str, str] = {}
    for color, terminal in spec.terminals.items():
        terminal_path = _child_path("$.terminals", color)
        a, b = terminal.endpoints
        if a == b:
            raise _error(f"{terminal_path}.endpoints", "must reference two distinct channels")
        for channel_id in terminal.endpoints:
            if channel_id not in topology.channels:
                raise _error(
                    f"{terminal_path}.endpoints",
                    f"references unknown channel {channel_id!r}",
                )
            previous = terminal_channels.get(channel_id)
            if previous is not None:
                raise _error(
                    f"{terminal_path}.endpoints",
                    f"channel {channel_id!r} is already a terminal for {previous!r}",
                )
            terminal_channels[channel_id] = color

    for cell_id, override in spec.rules.coverage.overrides.items():
        override_path = _child_path("$.rules.coverage.overrides", cell_id)
        if cell_id not in topology.cells:
            raise _error(override_path, "references an unknown cell")
        channel_count = len(channels_by_cell[cell_id])
        if override.min_used_channels is not None and override.min_used_channels > channel_count:
            raise _error(override_path, "min_used_channels exceeds the cell's channel count")
        if override.max_used_channels is not None and override.max_used_channels > channel_count:
            raise _error(override_path, "max_used_channels exceeds the cell's channel count")

    display = spec.display
    layer_ids = set(display.layers)

    def validate_layer(layer: Optional[str], path: str) -> None:
        if layer is not None and layer not in layer_ids:
            raise _error(path, f"references undeclared display layer {layer!r}")

    for cell_id, item in display.cells.items():
        if cell_id not in topology.cells:
            raise _error(_child_path("$.display.cells", cell_id), "references an unknown cell")
        validate_layer(item.layer, _child_path("$.display.cells", cell_id) + ".layer")
    for channel_id, item in display.channels.items():
        if channel_id not in topology.channels:
            raise _error(_child_path("$.display.channels", channel_id), "references an unknown channel")
        validate_layer(item.layer, _child_path("$.display.channels", channel_id) + ".layer")
    for channel_id, ports in display.ports.items():
        channel_path = _child_path("$.display.ports", channel_id)
        if channel_id not in topology.channels:
            raise _error(channel_path, "references an unknown channel")
        for port_id, item in ports.items():
            port_path = _child_path(channel_path, port_id)
            if port_id not in topology.channels[channel_id].ports:
                raise _error(port_path, "references an unknown port")
            validate_layer(item.layer, port_path + ".layer")
    for adjacency_id, item in display.adjacencies.items():
        if adjacency_id not in adjacency_ids:
            raise _error(
                _child_path("$.display.adjacencies", adjacency_id),
                "references an unknown adjacency",
            )
        validate_layer(item.layer, _child_path("$.display.adjacencies", adjacency_id) + ".layer")


def parse_v2_dict(raw: Mapping[str, Any]) -> PuzzleSpec:
    obj = _expect_object(raw, "$")
    _check_keys(
        obj,
        "$",
        required=("format", "schema_version", "topology", "terminals"),
        optional=("rules", "display", "catalog", "meta", "extensions"),
    )
    format_name = _expect_string(obj["format"], "$.format")
    if format_name != FORMAT_NAME:
        raise _error("$.format", f"must be {FORMAT_NAME!r}")
    schema_version = _expect_int(obj["schema_version"], "$.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise _error("$.schema_version", f"unsupported version {schema_version}; expected {SCHEMA_VERSION}")

    terminals_obj = _expect_object(obj["terminals"], "$.terminals")
    terminals: Dict[str, TerminalSpec] = {}
    for color in sorted(terminals_obj):
        _expect_string(color, "$.terminals key")
        terminals[color] = _parse_terminal(terminals_obj[color], _child_path("$.terminals", color))

    spec = PuzzleSpec(
        topology=_parse_topology(obj["topology"], "$.topology"),
        terminals=terminals,
        rules=_parse_rules(obj.get("rules", {}), "$.rules"),
        display=_parse_display(obj.get("display", {}), "$.display"),
        catalog=_parse_catalog(obj.get("catalog", {}), "$.catalog"),
        meta=_json_object(obj.get("meta", {}), "$.meta"),
        extensions=_json_object(obj.get("extensions", {}), "$.extensions"),
        format=format_name,
        schema_version=schema_version,
    )
    _validate_references(spec)
    return spec


def parse_v2_json(text: str) -> PuzzleSpec:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaV2Error(f"Invalid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise _error("$", "must be an object")
    return parse_v2_dict(raw)


def _port_to_dict(port: PortSpec) -> Dict[str, Any]:
    return {"kind": port.kind, "data": copy.deepcopy(port.data)}


def _position_to_list(position: Position) -> list[float]:
    return [float(position[0]), float(position[1]), float(position[2])]


def _display_item_base(layer: Optional[str], data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"data": copy.deepcopy(data)}
    if layer is not None:
        out["layer"] = layer
    return out


def _catalog_pack_to_dict(pack: CatalogPackSpec) -> Dict[str, Any]:
    out: Dict[str, Any] = {"data": copy.deepcopy(pack.data)}
    if pack.id is not None:
        out["id"] = pack.id
    if pack.name is not None:
        out["name"] = pack.name
    return out


def _catalog_level_to_dict(level: CatalogLevelSpec) -> Dict[str, Any]:
    out: Dict[str, Any] = {"data": copy.deepcopy(level.data)}
    if level.id is not None:
        out["id"] = level.id
    if level.number is not None:
        out["number"] = level.number
    if level.name is not None:
        out["name"] = level.name
    return out


def _display_size_to_dict(size: DisplaySizeSpec) -> Dict[str, Any]:
    out: Dict[str, Any] = {"data": copy.deepcopy(size.data)}
    if size.label is not None:
        out["label"] = size.label
    if size.width is not None:
        out["width"] = size.width
    if size.height is not None:
        out["height"] = size.height
    if size.unit is not None:
        out["unit"] = size.unit
    return out


def spec_to_dict(spec: PuzzleSpec) -> Dict[str, Any]:
    topology = spec.topology
    topology_obj: Dict[str, Any] = {
        "cells": {
            cell_id: {"kind": cell.kind, "data": copy.deepcopy(cell.data)}
            for cell_id, cell in sorted(topology.cells.items())
        },
        "channels": {
            channel_id: {
                "cell": channel.cell,
                "kind": channel.kind,
                "ports": {
                    port_id: _port_to_dict(port)
                    for port_id, port in sorted(channel.ports.items())
                },
                "data": copy.deepcopy(channel.data),
            }
            for channel_id, channel in sorted(topology.channels.items())
        },
        "adjacencies": [
            {
                "id": adjacency.id,
                "a": {"channel": adjacency.a.channel, "port": adjacency.a.port},
                "b": {"channel": adjacency.b.channel, "port": adjacency.b.port},
                "kind": adjacency.kind,
                "state": adjacency.state,
                "group": adjacency.group,
                "data": copy.deepcopy(adjacency.data),
            }
            for adjacency in sorted(topology.adjacencies, key=lambda item: item.id)
        ],
        "data": copy.deepcopy(topology.data),
    }
    if topology.template is not None:
        topology_obj["template"] = {
            "id": topology.template.id,
            "parameters": copy.deepcopy(topology.template.parameters),
        }

    display = spec.display
    display_obj: Dict[str, Any] = {
        "dimension": display.dimension,
        "cells": {},
        "channels": {},
        "ports": {},
        "adjacencies": {},
        "layers": list(display.layers),
        "data": copy.deepcopy(display.data),
    }
    for cell_id, item in sorted(display.cells.items()):
        value = _display_item_base(item.layer, item.data)
        if item.position is not None:
            value["position"] = _position_to_list(item.position)
        value["polygon"] = [_position_to_list(position) for position in item.polygon]
        if item.face is not None:
            value["face"] = item.face
        if item.z_index is not None:
            value["z_index"] = item.z_index
        display_obj["cells"][cell_id] = value
    for channel_id, item in sorted(display.channels.items()):
        value = _display_item_base(item.layer, item.data)
        if item.position is not None:
            value["position"] = _position_to_list(item.position)
        display_obj["channels"][channel_id] = value
    for channel_id, ports in sorted(display.ports.items()):
        display_obj["ports"][channel_id] = {}
        for port_id, item in sorted(ports.items()):
            value = _display_item_base(item.layer, item.data)
            if item.position is not None:
                value["position"] = _position_to_list(item.position)
            if item.normal is not None:
                value["normal"] = _position_to_list(item.normal)
            display_obj["ports"][channel_id][port_id] = value
    for adjacency_id, item in sorted(display.adjacencies.items()):
        value = _display_item_base(item.layer, item.data)
        value["points"] = [_position_to_list(position) for position in item.points]
        display_obj["adjacencies"][adjacency_id] = value

    catalog = spec.catalog
    catalog_obj: Dict[str, Any] = {
        "mechanics": list(catalog.mechanics),
        "data": copy.deepcopy(catalog.data),
    }
    if catalog.app is not None:
        catalog_obj["app"] = catalog.app
    if catalog.variant is not None:
        catalog_obj["variant"] = catalog.variant
    if catalog.pack is not None:
        catalog_obj["pack"] = _catalog_pack_to_dict(catalog.pack)
    if catalog.level is not None:
        catalog_obj["level"] = _catalog_level_to_dict(catalog.level)
    if catalog.mode is not None:
        catalog_obj["mode"] = catalog.mode
    if catalog.display_size is not None:
        catalog_obj["display_size"] = _display_size_to_dict(catalog.display_size)

    return {
        "format": spec.format,
        "schema_version": spec.schema_version,
        "topology": topology_obj,
        "terminals": {
            color: {
                "endpoints": list(terminal.endpoints),
                "color": terminal.color,
                "data": copy.deepcopy(terminal.data),
            }
            for color, terminal in sorted(spec.terminals.items())
        },
        "rules": {
            "coverage": {
                "mode": spec.rules.coverage.mode,
                "overrides": {
                    cell_id: {
                        **(
                            {"min_used_channels": override.min_used_channels}
                            if override.min_used_channels is not None
                            else {}
                        ),
                        **(
                            {"max_used_channels": override.max_used_channels}
                            if override.max_used_channels is not None
                            else {}
                        ),
                    }
                    for cell_id, override in sorted(spec.rules.coverage.overrides.items())
                },
            },
            "paths": {
                "endpoint_degree": spec.rules.paths.endpoint_degree,
                "internal_degree": spec.rules.paths.internal_degree,
                "connected": spec.rules.paths.connected,
            },
            "multi_channel_cell_color_policy": spec.rules.multi_channel_cell_color_policy,
        },
        "display": display_obj,
        "catalog": catalog_obj,
        "meta": copy.deepcopy(spec.meta),
        "extensions": copy.deepcopy(spec.extensions),
    }


def spec_to_json(spec: PuzzleSpec, *, indent: Optional[int] = 2) -> str:
    normalized = parse_v2_dict(spec_to_dict(spec))
    return json.dumps(
        spec_to_dict(normalized),
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def compile_puzzle_spec(spec: PuzzleSpec) -> "Puzzle":
    # Reparse a canonical representation so directly constructed dataclasses receive
    # the same strict validation as JSON-loaded documents.
    normalized = parse_v2_dict(spec_to_dict(spec))
    rules = normalized.rules
    if rules.coverage.overrides:
        raise _error(
            "$.rules.coverage.overrides",
            "per-cell coverage overrides are not supported by the current runtime compiler",
        )
    if rules.paths != PathRulesSpec():
        raise _error(
            "$.rules.paths",
            "the current runtime compiler requires endpoint_degree=1, internal_degree=2, connected=true",
        )
    if rules.multi_channel_cell_color_policy != "distinct":
        raise _error(
            "$.rules.multi_channel_cell_color_policy",
            "the current runtime compiler supports only 'distinct'",
        )

    graph = Graph()
    display = normalized.display
    for channel_id, channel in sorted(normalized.topology.channels.items()):
        channel_display = display.channels.get(channel_id)
        cell_display = display.cells.get(channel.cell)
        if channel_display is not None and channel_display.position is not None:
            position = channel_display.position
        elif cell_display is not None and cell_display.position is not None:
            position = cell_display.position
        else:
            position = (0.0, 0.0, 0.0)
        data = copy.deepcopy(channel.data)
        data["cell"] = channel.cell
        data["tile"] = channel.cell
        data["ports"] = sorted(channel.ports)
        graph.add_node(Node(id=channel_id, pos=position, kind=channel.kind, data=data))

    for adjacency in normalized.topology.adjacencies:
        if adjacency.state == "open":
            graph.add_edge(adjacency.a.channel, adjacency.b.channel)

    channels_by_cell: Dict[str, list[str]] = {cell_id: [] for cell_id in normalized.topology.cells}
    for channel_id, channel in normalized.topology.channels.items():
        channels_by_cell[channel.cell].append(channel_id)
    tiles = {
        cell_id: sorted(channel_ids)
        for cell_id, channel_ids in sorted(channels_by_cell.items())
    }
    terminals = {
        color: terminal.endpoints
        for color, terminal in sorted(normalized.terminals.items())
    }
    meta = copy.deepcopy(normalized.meta)
    terminal_colors = {
        color: terminal.color
        for color, terminal in normalized.terminals.items()
        if terminal.color is not None
    }
    if terminal_colors and "terminal_colors" not in meta:
        meta["terminal_colors"] = terminal_colors

    from .puzzle import Puzzle

    return Puzzle(
        graph=graph,
        tiles=tiles,
        terminals=terminals,
        fill=rules.coverage.mode == "all-cells",
        meta=meta,
        source_spec=normalized,
    )


__all__ = [
    "ADJACENCY_KINDS",
    "ADJACENCY_STATES",
    "COVERAGE_MODES",
    "FORMAT_NAME",
    "MULTI_CHANNEL_COLOR_POLICIES",
    "SCHEMA_VERSION",
    "AdjacencyEndpoint",
    "AdjacencySpec",
    "CatalogLevelSpec",
    "CatalogPackSpec",
    "CatalogSpec",
    "CellCoverageOverride",
    "CellDisplaySpec",
    "CellSpec",
    "ChannelDisplaySpec",
    "ChannelSpec",
    "CoverageSpec",
    "DisplaySizeSpec",
    "DisplaySpec",
    "PathRulesSpec",
    "PortDisplaySpec",
    "PortSpec",
    "PuzzleSpec",
    "RulesSpec",
    "SchemaV2Error",
    "TemplateSpec",
    "TerminalSpec",
    "TopologySpec",
    "compile_puzzle_spec",
    "has_v2_marker",
    "parse_v2_dict",
    "parse_v2_json",
    "spec_to_dict",
    "spec_to_json",
]
