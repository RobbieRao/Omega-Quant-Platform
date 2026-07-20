from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import os
import sys
import tempfile
import types
from functools import wraps
from pathlib import Path
from typing import Any

def _install_array_api_compat_shim() -> None:
    if "sklearn.externals.array_api_compat" in sys.modules:
        return

    import numpy as np

    numpy_namespace = types.ModuleType("sklearn.externals.array_api_compat.numpy")
    numpy_namespace.__dict__.update(np.__dict__)
    numpy_namespace.__name__ = "sklearn.externals.array_api_compat.numpy"

    def _namespace_info() -> Any:
        class _Info:
            @staticmethod
            def dtypes(kind: str | None = None, device: Any | None = None) -> dict[str, Any]:
                return {
                    "float64": np.dtype("float64"),
                    "float32": np.dtype("float32"),
                    "float16": np.dtype("float16"),
                    "int64": np.dtype("int64"),
                    "int32": np.dtype("int32"),
                    "bool": np.dtype("bool"),
                }

        return _Info()

    numpy_namespace.__array_namespace_info__ = _namespace_info  # type: ignore[attr-defined]

    array_api_compat = types.ModuleType("sklearn.externals.array_api_compat")
    array_api_compat.__dict__.update(
        {
            "__doc__": "Lightweight compatibility shim for scikit-learn.",
            "__package__": "sklearn.externals",
            "__name__": "sklearn.externals.array_api_compat",
            "numpy": numpy_namespace,
        }
    )

    def get_namespace(*arrays: Any) -> types.ModuleType:
        arrays = [array for array in arrays if array is not None]
        if not arrays:
            return numpy_namespace
        for array in arrays:
            namespace_getter = getattr(array, "__array_namespace__", None)
            if callable(namespace_getter):
                try:
                    namespace = namespace_getter()
                except TypeError:
                    namespace = namespace_getter(api_version=None)
                if namespace is not None:
                    return namespace
        return numpy_namespace

    def _namespace_name(xp: Any) -> str:
        return getattr(xp, "__name__", "")

    array_api_compat.get_namespace = get_namespace  # type: ignore[attr-defined]
    array_api_compat.is_numpy_namespace = lambda xp: _namespace_name(xp) in {  # type: ignore[attr-defined]
        "numpy",
        "sklearn.externals.array_api_compat.numpy",
    }
    array_api_compat.is_torch_namespace = lambda xp: _namespace_name(xp) == "torch"  # type: ignore[attr-defined]
    array_api_compat.is_cupy_namespace = lambda xp: _namespace_name(xp) == "cupy"  # type: ignore[attr-defined]
    array_api_compat.is_array_api_obj = lambda array: hasattr(array, "__array_namespace__")  # type: ignore[attr-defined]

    array_api_extra = types.ModuleType("sklearn.externals.array_api_extra")
    array_api_extra.__dict__.update(
        {
            "__doc__": "Lightweight compatibility shim for scikit-learn.",
            "__package__": "sklearn.externals",
            "__name__": "sklearn.externals.array_api_extra",
        }
    )

    def _extra_getattr(name: str) -> Any:
        return getattr(numpy_namespace, name)

    array_api_extra.__getattr__ = _extra_getattr  # type: ignore[attr-defined]

    sys.modules["sklearn.externals.array_api_compat"] = array_api_compat
    sys.modules["sklearn.externals.array_api_compat.numpy"] = numpy_namespace
    sys.modules["sklearn.externals.array_api_extra"] = array_api_extra

    packaging_pkg = types.ModuleType("sklearn.externals._packaging")
    packaging_pkg.__dict__.update(
        {
            "__doc__": "Compatibility shim for vendored packaging utilities.",
            "__package__": "sklearn.externals",
            "__name__": "sklearn.externals._packaging",
            "__path__": [],
        }
    )
    packaging_version = types.ModuleType("sklearn.externals._packaging.version")
    packaging_version.__dict__.update(
        {
            "__doc__": "Compatibility shim for vendored packaging.version.",
            "__package__": "sklearn.externals._packaging",
            "__name__": "sklearn.externals._packaging.version",
        }
    )
    from packaging.version import parse as packaging_parse

    packaging_version.parse = packaging_parse  # type: ignore[attr-defined]
    packaging_pkg.version = packaging_version  # type: ignore[attr-defined]
    sys.modules["sklearn.externals._packaging"] = packaging_pkg
    sys.modules["sklearn.externals._packaging.version"] = packaging_version


def _install_cvxpy_complex2real_shim() -> None:
    if "cvxpy.reductions.complex2real.canonicalizers" in sys.modules:
        return

    canonicalizers = types.ModuleType("cvxpy.reductions.complex2real.canonicalizers")
    canonicalizers.__dict__.update(
        {
            "__doc__": "Compatibility shim for cvxpy complex-to-real canonicalizers.",
            "__package__": "cvxpy.reductions.complex2real",
            "__name__": "cvxpy.reductions.complex2real.canonicalizers",
        }
    )

    class Complex2RealCanonMethods:
        def __init__(self) -> None:
            self._parameters: dict[Any, tuple[Any, Any]] = {}
            self._variables: dict[Any, tuple[Any, Any]] = {}

        def __contains__(self, key: object) -> bool:
            return True

        def __getitem__(self, key: object):
            def _canonicalize(expr: Any, real_args: list[Any], imag_args: list[Any], real2imag: dict[int, int]):
                return expr.copy(real_args), None

            return _canonicalize

        def copy(self) -> "Complex2RealCanonMethods":
            clone = Complex2RealCanonMethods()
            clone._parameters = dict(self._parameters)
            clone._variables = dict(self._variables)
            return clone

    canonicalizers.Complex2RealCanonMethods = Complex2RealCanonMethods  # type: ignore[attr-defined]
    class _CanonMethods(dict):
        def __contains__(self, key: object) -> bool:
            return True

        def __getitem__(self, key: object):
            def _canonicalize(expr: Any, real_args: list[Any], imag_args: list[Any], real2imag: dict[int, int]):
                return expr.copy(real_args), None

            return _canonicalize

    canonicalizers.CANON_METHODS = _CanonMethods()  # type: ignore[attr-defined]
    sys.modules["cvxpy.reductions.complex2real.canonicalizers"] = canonicalizers


def _install_cvxpy_dcp2cone_shim() -> None:
    if "cvxpy.reductions.dcp2cone.canonicalizers" in sys.modules:
        return

    def _canonicalize(expr: Any, args: list[Any], **_: Any):
        return expr.copy(args).canonical_form

    class _CanonMethods(dict):
        def __contains__(self, key: object) -> bool:
            return True

        def __getitem__(self, key: object):
            return _canonicalize

    canonicalizers = types.ModuleType("cvxpy.reductions.dcp2cone.canonicalizers")
    canonicalizers.__dict__.update(
        {
            "__doc__": "Compatibility shim for cvxpy DCP canonicalizers.",
            "__package__": "cvxpy.reductions.dcp2cone",
            "__name__": "cvxpy.reductions.dcp2cone.canonicalizers",
            "__path__": [],
            "CANON_METHODS": _CanonMethods(),
        }
    )

    quad = types.ModuleType("cvxpy.reductions.dcp2cone.canonicalizers.quad")
    quad.__dict__.update(
        {
            "__doc__": "Compatibility shim for cvxpy quadratic canonicalizers.",
            "__package__": "cvxpy.reductions.dcp2cone.canonicalizers",
            "__name__": "cvxpy.reductions.dcp2cone.canonicalizers.quad",
            "QUAD_CANON_METHODS": _CanonMethods(),
        }
    )

    canonicalizers.quad = quad  # type: ignore[attr-defined]
    sys.modules["cvxpy.reductions.dcp2cone.canonicalizers"] = canonicalizers
    sys.modules["cvxpy.reductions.dcp2cone.canonicalizers.quad"] = quad


def _install_cvxpy_dgp2dcp_shim() -> None:
    if "cvxpy.reductions.dgp2dcp.canonicalizers" in sys.modules:
        return

    def _canonicalize(expr: Any, args: list[Any], **_: Any):
        return expr.copy(args).canonical_form

    class _CanonMethods(dict):
        def __init__(self, reduction: Any | None = None) -> None:
            super().__init__()
            self.reduction = reduction
            self._parameters: dict[Any, Any] = {}
            self._variables: dict[Any, Any] = {}

        def __contains__(self, key: object) -> bool:
            return True

        def __getitem__(self, key: object):
            return _canonicalize

    canonicalizers = types.ModuleType("cvxpy.reductions.dgp2dcp.canonicalizers")
    canonicalizers.__dict__.update(
        {
            "__doc__": "Compatibility shim for cvxpy DGP canonicalizers.",
            "__package__": "cvxpy.reductions.dgp2dcp",
            "__name__": "cvxpy.reductions.dgp2dcp.canonicalizers",
            "DgpCanonMethods": _CanonMethods,
        }
    )
    sys.modules["cvxpy.reductions.dgp2dcp.canonicalizers"] = canonicalizers


def _disable_numba_cache() -> None:
    try:
        import numba
    except Exception:
        return

    def _wrap_decorator(original):
        @wraps(original)
        def wrapped(*args: Any, **kwargs: Any):
            if kwargs.get("cache") is True:
                kwargs = dict(kwargs)
                kwargs["cache"] = False
            return original(*args, **kwargs)

        return wrapped

    for attr_name in ("jit", "njit", "vectorize", "guvectorize"):
        original = getattr(numba, attr_name, None)
        if callable(original):
            patched = _wrap_decorator(original)
            setattr(numba, attr_name, patched)
            try:
                decorators = importlib.import_module("numba.core.decorators")
            except Exception:
                continue
            if hasattr(decorators, attr_name):
                setattr(decorators, attr_name, patched)


class _CompatModuleLoader(importlib.abc.Loader):
    def __init__(self, fullname: str) -> None:
        self.fullname = fullname

    def create_module(self, spec):
        return types.ModuleType(spec.name)

    def exec_module(self, module) -> None:
        from packaging.version import parse as packaging_parse

        name = module.__name__
        module.__dict__.setdefault("__doc__", "Compatibility shim.")
        if name == "sklearn.utils._repr_html":
            module.__dict__.update({"__package__": "sklearn.utils", "__path__": []})
            return
        if name == "sklearn.utils._repr_html.base":
            module.__dict__.update({"__package__": "sklearn.utils._repr_html"})

            class _HTMLDocumentationLinkMixin:
                pass

            class ReprHTMLMixin:
                def _repr_html_(self):
                    return estimator_html_repr(self)

            def estimator_html_repr(estimator):
                return f"<pre>{estimator!r}</pre>"

            module._HTMLDocumentationLinkMixin = _HTMLDocumentationLinkMixin  # type: ignore[attr-defined]
            module.ReprHTMLMixin = ReprHTMLMixin  # type: ignore[attr-defined]
            module.estimator_html_repr = estimator_html_repr  # type: ignore[attr-defined]
            return
        if name == "sklearn.utils._repr_html.estimator":
            module.__dict__.update({"__package__": "sklearn.utils._repr_html"})

            def estimator_html_repr(estimator):
                return f"<pre>{estimator!r}</pre>"

            class _VisualBlock:
                def __init__(self, kind=None, estimators=None, names=None, name_details=None):
                    self.kind = kind
                    self.estimators = estimators
                    self.names = names
                    self.name_details = name_details

            module.estimator_html_repr = estimator_html_repr  # type: ignore[attr-defined]
            module._VisualBlock = _VisualBlock  # type: ignore[attr-defined]
            return
        if name == "sklearn.utils._repr_html.params":
            module.__dict__.update({"__package__": "sklearn.utils._repr_html"})

            class ParamsDict(dict):
                pass

            module.ParamsDict = ParamsDict  # type: ignore[attr-defined]
            return
        if name == "sklearn.metrics.cluster":
            module.__dict__.update({"__package__": "sklearn.metrics"})

            def _zero(*args, **kwargs):
                return 0.0

            def _zeros(*args, **kwargs):
                import numpy as np

                return np.asarray([])

            def _pair_confusion_matrix(*args, **kwargs):
                import numpy as np

                return np.zeros((2, 2), dtype=float)

            def _homogeneity_completeness_v_measure(*args, **kwargs):
                return 0.0, 0.0, 0.0

            module.adjusted_mutual_info_score = _zero  # type: ignore[attr-defined]
            module.adjusted_rand_score = _zero  # type: ignore[attr-defined]
            module.calinski_harabasz_score = _zero  # type: ignore[attr-defined]
            module.completeness_score = _zero  # type: ignore[attr-defined]
            module.consensus_score = _zero  # type: ignore[attr-defined]
            module.davies_bouldin_score = _zero  # type: ignore[attr-defined]
            module.fowlkes_mallows_score = _zero  # type: ignore[attr-defined]
            module.homogeneity_completeness_v_measure = _homogeneity_completeness_v_measure  # type: ignore[attr-defined]
            module.homogeneity_score = _zero  # type: ignore[attr-defined]
            module.mutual_info_score = _zero  # type: ignore[attr-defined]
            module.normalized_mutual_info_score = _zero  # type: ignore[attr-defined]
            module.pair_confusion_matrix = _pair_confusion_matrix  # type: ignore[attr-defined]
            module.rand_score = _zero  # type: ignore[attr-defined]
            module.silhouette_samples = _zeros  # type: ignore[attr-defined]
            module.silhouette_score = _zero  # type: ignore[attr-defined]
            module.v_measure_score = _zero  # type: ignore[attr-defined]
            return
        if name == "sklearn.metrics._plot":
            module.__dict__.update({"__package__": "sklearn.metrics", "__path__": []})
            return
        if name.startswith("sklearn.metrics._plot."):
            module.__dict__.update({"__package__": "sklearn.metrics._plot"})
            display_name = name.rsplit(".", 1)[-1]
            display_map = {
                "confusion_matrix": "ConfusionMatrixDisplay",
                "det_curve": "DetCurveDisplay",
                "precision_recall_curve": "PrecisionRecallDisplay",
                "regression": "PredictionErrorDisplay",
                "roc_curve": "RocCurveDisplay",
            }
            class_name = display_map.get(
                display_name,
                "".join(part.capitalize() for part in display_name.split("_")) + "Display",
            )

            class _Display:
                def __init__(self, *args, **kwargs):
                    pass

            setattr(module, class_name, _Display)
            return
        if name == "sklearn.metrics._pairwise_distances_reduction":
            module.__dict__.update({"__package__": "sklearn.metrics"})

            class ArgKmin:
                @staticmethod
                def valid_metrics():
                    return [
                        "euclidean",
                        "l2",
                        "sqeuclidean",
                        "manhattan",
                        "cityblock",
                        "cosine",
                    ]

                @staticmethod
                def is_usable_for(X, Y, metric):
                    return metric in ArgKmin.valid_metrics()

                @staticmethod
                def compute(
                    *,
                    X,
                    Y,
                    k,
                    metric,
                    metric_kwargs,
                    strategy,
                    return_distance,
                ):
                    import numpy as np
                    from scipy.spatial.distance import cdist

                    metric_name = "euclidean" if metric == "l2" else metric
                    if metric_name == "sqeuclidean":
                        metric_name = "euclidean"
                        squared = True
                    else:
                        squared = bool(metric_kwargs.get("squared", False))
                    distances = cdist(np.asarray(X), np.asarray(Y), metric=metric_name)
                    if squared:
                        distances = distances ** 2
                    indices = np.argmin(distances, axis=1)
                    values = distances[np.arange(distances.shape[0]), indices]
                    values = values.reshape(-1, 1)
                    indices = indices.reshape(-1, 1)
                    if return_distance:
                        return values, indices
                    return indices

            class RadiusNeighbors:
                @staticmethod
                def valid_metrics():
                    return ArgKmin.valid_metrics()

                @staticmethod
                def is_usable_for(X, Y, metric):
                    return metric in RadiusNeighbors.valid_metrics()

                @staticmethod
                def compute(
                    *,
                    X,
                    Y,
                    radius,
                    metric,
                    metric_kwargs,
                    strategy,
                    return_distance,
                    sort_results,
                ):
                    import numpy as np
                    from scipy.spatial.distance import cdist

                    metric_name = "euclidean" if metric == "l2" else metric
                    if metric_name == "sqeuclidean":
                        metric_name = "euclidean"
                        squared = True
                    else:
                        squared = bool(metric_kwargs.get("squared", False))
                    distances = cdist(np.asarray(X), np.asarray(Y), metric=metric_name)
                    if squared:
                        distances = distances ** 2
                    neighbors = []
                    neighbor_distances = []
                    for row in distances:
                        idx = np.where(row <= radius)[0]
                        dist = row[idx]
                        if sort_results and len(idx):
                            order = np.argsort(dist)
                            idx = idx[order]
                            dist = dist[order]
                        neighbors.append(idx)
                        neighbor_distances.append(dist)
                    indices = np.array([np.asarray(idx) for idx in neighbors], dtype=object)
                    dists = np.array([np.asarray(dist) for dist in neighbor_distances], dtype=object)
                    if return_distance:
                        return dists, indices
                    return indices

            module.ArgKmin = ArgKmin  # type: ignore[attr-defined]
            module.RadiusNeighbors = RadiusNeighbors  # type: ignore[attr-defined]
            module.ArgKminClassMode = ArgKmin  # type: ignore[attr-defined]
            module.RadiusNeighborsClassMode = RadiusNeighbors  # type: ignore[attr-defined]
            return
        if name == "sklearn.linear_model._glm":
            module.__dict__.update({"__package__": "sklearn.linear_model", "__path__": []})

            class _GLMBase:
                def __init__(self, *args, **kwargs):
                    pass

                def fit(self, X, y, sample_weight=None):
                    self.n_features_in_ = getattr(X, "shape", [None, None])[1]
                    return self

                def predict(self, X):
                    import numpy as np

                    return np.zeros(getattr(X, "shape", [0])[0])

            class GammaRegressor(_GLMBase):
                pass

            class PoissonRegressor(_GLMBase):
                pass

            class TweedieRegressor(_GLMBase):
                pass

            class NewtonCholeskySolver:
                pass

            module.GammaRegressor = GammaRegressor  # type: ignore[attr-defined]
            module.PoissonRegressor = PoissonRegressor  # type: ignore[attr-defined]
            module.TweedieRegressor = TweedieRegressor  # type: ignore[attr-defined]
            module.NewtonCholeskySolver = NewtonCholeskySolver  # type: ignore[attr-defined]
            return
        if name == "sklearn.linear_model._glm.glm":
            module.__dict__.update({"__package__": "sklearn.linear_model._glm"})

            class NewtonCholeskySolver:
                pass

            module.NewtonCholeskySolver = NewtonCholeskySolver  # type: ignore[attr-defined]
            return

        if name == "sklearn.externals._packaging":
            module.__dict__.update({"__package__": "sklearn.externals", "__path__": []})
            return
        if name == "sklearn.externals._packaging.version":
            module.__dict__.update({"__package__": "sklearn.externals._packaging"})
            module.parse = packaging_parse  # type: ignore[attr-defined]
            return

        if name.startswith("cvxpy.reductions.dcp2cone.canonicalizers"):
            module.__dict__.update(
                {
                    "__package__": "cvxpy.reductions.dcp2cone",
                    "__path__": [] if name == "cvxpy.reductions.dcp2cone.canonicalizers" else None,
                }
            )

            def _canonicalize(expr: Any, args: list[Any], **_: Any):
                return expr.copy(args).canonical_form

            class _CanonMethods(dict):
                def __contains__(self, key: object) -> bool:
                    return True

                def __getitem__(self, key: object):
                    return _canonicalize

            module.CANON_METHODS = _CanonMethods()  # type: ignore[attr-defined]
            if name.endswith(".quad"):
                module.QUAD_CANON_METHODS = _CanonMethods()  # type: ignore[attr-defined]
            return

        if name == "cvxpy.reductions.complex2real.canonicalizers":
            module.__dict__.update(
                {"__package__": "cvxpy.reductions.complex2real", "__path__": []}
            )

            def _canonicalize(expr: Any, real_args: list[Any], imag_args: list[Any], real2imag: dict[int, int]):
                return expr.copy(real_args), None

            class _CanonMethods(dict):
                def __contains__(self, key: object) -> bool:
                    return True

                def __getitem__(self, key: object):
                    return _canonicalize

            class Complex2RealCanonMethods:
                def __init__(self) -> None:
                    self._parameters: dict[Any, tuple[Any, Any]] = {}
                    self._variables: dict[Any, tuple[Any, Any]] = {}

                def __contains__(self, key: object) -> bool:
                    return True

                def __getitem__(self, key: object):
                    return _canonicalize

                def copy(self) -> "Complex2RealCanonMethods":
                    clone = Complex2RealCanonMethods()
                    clone._parameters = dict(self._parameters)
                    clone._variables = dict(self._variables)
                    return clone

            module.CANON_METHODS = _CanonMethods()  # type: ignore[attr-defined]
            module.Complex2RealCanonMethods = Complex2RealCanonMethods  # type: ignore[attr-defined]
            return

        if name == "cvxpy.reductions.dgp2dcp.canonicalizers":
            module.__dict__.update(
                {"__package__": "cvxpy.reductions.dgp2dcp", "__path__": []}
            )

            def _canonicalize(expr: Any, args: list[Any], **_: Any):
                return expr.copy(args).canonical_form

            class DgpCanonMethods(dict):
                def __init__(self, reduction: Any | None = None) -> None:
                    super().__init__()
                    self.reduction = reduction
                    self._parameters: dict[Any, Any] = {}
                    self._variables: dict[Any, Any] = {}

                def __contains__(self, key: object) -> bool:
                    return True

                def __getitem__(self, key: object):
                    return _canonicalize

            module.DgpCanonMethods = DgpCanonMethods  # type: ignore[attr-defined]
            return

        if name in {
            "cvxpy.reductions.solvers",
            "cvxpy.reductions.solvers.conic_solvers",
            "cvxpy.reductions.solvers.nlp_solvers",
            "cvxpy.reductions.solvers.qp_solvers",
        }:
            module.__dict__.update({"__path__": [], "__package__": name.rsplit(".", 1)[0]})
            return

        if name == "cvxpy.reductions.solvers.solving_chain_utils":
            module.__dict__.update({"__package__": "cvxpy.reductions.solvers"})

            def get_canon_backend(problem, canon_backend: str):
                if canon_backend is not None:
                    return canon_backend
                return "SCIPY"

            module.get_canon_backend = get_canon_backend  # type: ignore[attr-defined]
            return

        if name == "cvxpy.reductions.solvers.qp_solvers.qp_solver":
            module.__dict__.update({"__package__": "cvxpy.reductions.solvers.qp_solvers"})

            class QpSolver:
                MIP_CAPABLE = False
                BOUNDED_VARIABLES = False
                SOC_DIM3_ONLY = False
                PSD_TRIANGLE_KIND = None
                PSD_SQRT2_SCALING = None
                SUPPORTED_CONSTRAINTS = []
                MI_SUPPORTED_CONSTRAINTS = []

                def name(self) -> str:
                    return "QP"

                def import_solver(self):
                    raise ModuleNotFoundError("QP")

                def is_installed(self) -> bool:
                    return False

                def solve_via_data(self, data, warm_start: bool, verbose: bool, solver_opts, solver_cache=None):
                    raise NotImplementedError("QP")

                def cite(self, data):
                    return ""

                def supports_quad_obj(self) -> bool:
                    return True

            module.QpSolver = QpSolver  # type: ignore[attr-defined]
            return

        if name.startswith(
            (
                "cvxpy.reductions.solvers.conic_solvers.",
                "cvxpy.reductions.solvers.nlp_solvers.",
                "cvxpy.reductions.solvers.qp_solvers.",
            )
        ):
            base = name.rsplit(".", 1)[-1]
            root = base
            for suffix in ("_conif", "_nlpif", "_qpif"):
                if root.endswith(suffix):
                    root = root[: -len(suffix)]
                    break
            class_name = "_".join(part.upper() for part in root.split("_"))
            module.__dict__.update({"__package__": name.rsplit(".", 1)[0]})

            qp_base = sys.modules.get("cvxpy.reductions.solvers.qp_solvers.qp_solver")
            base_cls = getattr(qp_base, "QpSolver", object) if name.startswith("cvxpy.reductions.solvers.qp_solvers.") else object

            class SolverStub(base_cls):
                MIP_CAPABLE = False
                BOUNDED_VARIABLES = False
                SOC_DIM3_ONLY = False
                PSD_TRIANGLE_KIND = None
                PSD_SQRT2_SCALING = None
                SUPPORTED_CONSTRAINTS = []
                MI_SUPPORTED_CONSTRAINTS = []

                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    pass

                def name(self) -> str:
                    return class_name

                def import_solver(self):
                    raise ModuleNotFoundError(class_name)

                def is_installed(self) -> bool:
                    return False

                def solve_via_data(self, data, warm_start: bool, verbose: bool, solver_opts, solver_cache=None):
                    raise NotImplementedError(class_name)

                def cite(self, data):
                    return ""

                def supports_quad_obj(self) -> bool:
                    return False

            setattr(module, class_name, SolverStub)
            return

        raise ModuleNotFoundError(name)


class _CompatModuleFinder(importlib.abc.MetaPathFinder):
    PREFIXES = (
        "sklearn.utils._repr_html",
        "sklearn.metrics.cluster",
        "sklearn.metrics._plot",
        "sklearn.metrics._pairwise_distances_reduction",
        "sklearn.linear_model._glm",
        "sklearn.externals._packaging",
        "cvxpy.reductions.solvers.solving_chain_utils",
        "cvxpy.reductions.solvers.conic_solvers",
        "cvxpy.reductions.solvers.nlp_solvers",
        "cvxpy.reductions.solvers.qp_solvers",
        "cvxpy.reductions.dcp2cone.canonicalizers",
        "cvxpy.reductions.complex2real.canonicalizers",
        "cvxpy.reductions.dgp2dcp.canonicalizers",
    )

    def find_spec(self, fullname, path, target=None):
        if fullname in sys.modules:
            return None
        if not fullname.startswith(self.PREFIXES):
            return None
        is_package = fullname in {
            "sklearn.utils._repr_html",
            "sklearn.metrics.cluster",
            "sklearn.metrics._plot",
            "sklearn.metrics._pairwise_distances_reduction",
            "sklearn.linear_model._glm",
            "sklearn.externals._packaging",
            "cvxpy.reductions.solvers.conic_solvers",
            "cvxpy.reductions.solvers.nlp_solvers",
            "cvxpy.reductions.solvers.qp_solvers",
            "cvxpy.reductions.dcp2cone.canonicalizers",
            "cvxpy.reductions.complex2real.canonicalizers",
            "cvxpy.reductions.dgp2dcp.canonicalizers",
        }
        return importlib.machinery.ModuleSpec(
            fullname,
            _CompatModuleLoader(fullname),
            is_package=is_package,
        )


def install_runtime_shims() -> None:
    os.environ.setdefault(
        "MPLCONFIGDIR",
        str(Path(tempfile.gettempdir()) / "omega-quant-mpl"),
    )
    _disable_numba_cache()
