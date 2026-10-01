"""T6.3: every response the test suite receives is held against the OpenAPI spec.

Rather than a second suite that calls each endpoint once, the whole suite is the
contract suite: each test client's responses are checked as they arrive.

- A success must be a status the operation documents, in the media type it
  documents, and a JSON body must validate against the documented schema.
- An error must be JSON with FastAPI's `detail`.

Coverage is recorded too. Every operation in the spec must return at least one
checked success somewhere in the suite, or the run fails (see conftest).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi.routing import APIRoute
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

SPEC_URI = "urn:ivaas:openapi"


def _pointer(*parts: str) -> str:
    return "/".join(p.replace("~", "~0").replace("/", "~1") for p in parts)


class Contract:
    def __init__(self) -> None:
        self._apps: dict[int, tuple[dict, Registry, list[APIRoute]]] = {}
        self._validators: dict[tuple[int, str], Draft202012Validator] = {}
        #: (METHOD, path template) -> statuses seen
        self.seen: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.problems: list[str] = []
        self.operations: set[tuple[str, str]] = set()

    def _app(self, app: Any) -> tuple[dict, Registry, list[APIRoute]] | None:
        if not hasattr(app, "openapi"):
            return None
        known = self._apps.get(id(app))
        if known is None:
            spec = app.openapi()
            registry = Registry().with_resource(SPEC_URI, DRAFT202012.create_resource(spec))
            routes = [r for r in app.routes if isinstance(r, APIRoute) and r.include_in_schema]
            known = self._apps[id(app)] = (spec, registry, routes)
            for path, item in spec["paths"].items():
                for method in item:
                    self.operations.add((method.upper(), path))
        return known

    def _validator(self, app: Any, registry: Registry, pointer: str) -> Draft202012Validator:
        key = (id(app), pointer)
        if key not in self._validators:
            self._validators[key] = Draft202012Validator(
                {"$ref": f"{SPEC_URI}#/{pointer}"}, registry=registry
            )
        return self._validators[key]

    def check(self, app: Any, response: Any) -> list[str]:
        """Record the response; return what about it breaks the spec (empty if nothing)."""
        before = len(self.problems)
        self._check(app, response)
        return self.problems[before:]

    def _check(self, app: Any, response: Any) -> None:
        known = self._app(app)
        if known is None:
            return
        spec, registry, routes = known
        method, path = response.request.method, response.request.url.path
        route = next((r for r in routes if method in r.methods and r.path_regex.match(path)), None)
        if route is None:
            return  # not an operation in the spec: a static file, a socket, a made-up path
        template = route.path_format
        op = spec["paths"].get(template, {}).get(method.lower())
        if op is None:
            return
        status = response.status_code
        self.seen[(method, template)].add(status)
        where = f"{method} {template} -> {status}"
        ctype = response.headers.get("content-type", "").split(";")[0].strip()

        if status >= 400:
            try:
                body = response.json()
            except ValueError:
                self.problems.append(f"{where}: an error that is not JSON ({ctype})")
                return
            if not isinstance(body, dict) or "detail" not in body:
                self.problems.append(f"{where}: an error without 'detail': {str(body)[:120]}")
            return

        documented = op.get("responses", {})
        if str(status) not in documented:
            self.problems.append(f"{where}: status not in the spec ({sorted(documented)})")
            return
        content = documented[str(status)].get("content") or {}
        if not content:
            if response.content:
                self.problems.append(f"{where}: a body where the spec documents none")
            return
        family = ctype.split("/")[0] + "/*"
        if ctype not in content and family not in content and "*/*" not in content:
            sent = ctype or "no content type"
            self.problems.append(f"{where}: {sent}, spec says {list(content)}")
            return
        if ctype != "application/json":
            return
        pointer = _pointer("paths", template, method.lower(), "responses", str(status))
        pointer += "/content/application~1json/schema"
        errors = list(self._validator(app, registry, pointer).iter_errors(response.json()))
        for e in errors[:3]:
            at = "/".join(str(p) for p in e.absolute_path) or "(body)"
            self.problems.append(f"{where}: at {at}: {e.message[:160]}")

    def uncovered(self) -> list[tuple[str, str]]:
        """Operations no test got a checked success from."""
        ok = {k for k, statuses in self.seen.items() if any(200 <= s < 300 for s in statuses)}
        return sorted(self.operations - ok, key=lambda k: (k[1], k[0]))


CONTRACT = Contract()
