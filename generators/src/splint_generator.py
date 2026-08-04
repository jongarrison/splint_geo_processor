"""Base class, result container, and phase-tracking infrastructure for Python-based
splint generators.

Every Python splint design definition (RelativeMotion, future designs) subclasses
SplintGenerator and implements generate(). The production runner and dev harness both
call generate() with the same signature; the runner handles job I/O and mesh export,
the harness handles baking and preview layout.
"""


class StopAfterPhase(Exception):
    """Raised by PhaseTracker when stop_after threshold is reached. Not an error."""
    def __init__(self, phase_number, phase_title):
        self.phase = phase_number
        self.title = phase_title
        super(StopAfterPhase, self).__init__(
            "stopped after phase {0}: {1}".format(phase_number, phase_title))


class PhaseTracker(object):
    """Tracks generator phases, populates a debug dict, enforces stop_after.

    Usage inside a generate() method:
        tracker = PhaseTracker(debug=debug, stop_after=stop_after, log_fn=log)
        ...compute phase 1 geometry...
        tracker.log_phase(1.0, "finger positions",
            mcp_points=mcp_points, p1_lines=p1_lines)
        ...compute more...
        tracker.add(extra_curve=some_curve)   # ad-hoc item, no phase boundary
    """

    def __init__(self, debug=None, stop_after=None, log_fn=None):
        self._debug = debug
        self._stop_after = stop_after
        self._log = log_fn or (lambda msg: None)
        self.phases = []  # ordered list: [{number, title, keys}]

    def log_phase(self, number, title, **geometry):
        """Record a completed phase boundary.

        Stores geometry in the debug dict, appends phase metadata, logs the phase,
        and raises StopAfterPhase if number >= stop_after.
        """
        self._log("Phase {0}: {1}".format(number, title))
        entry = {"number": number, "title": title, "keys": list(geometry.keys())}
        self.phases.append(entry)

        if self._debug is not None:
            self._debug.update(geometry)
            self._debug["_phases"] = list(self.phases)

        if self._stop_after is not None and number >= self._stop_after:
            raise StopAfterPhase(number, title)

    def add(self, **geometry):
        """Add ad-hoc geometry to the debug dict without marking a phase boundary.

        Use for intermediate items within a phase that are useful for previewing
        but don't represent a logical stopping point.
        """
        if self._debug is not None:
            self._debug.update(geometry)
            # Append keys to the most recent phase entry so auto-preview picks them up.
            if self.phases:
                self.phases[-1]["keys"].extend(geometry.keys())
                self._debug["_phases"] = list(self.phases)


class SplintResult(object):
    """Output of a splint generator's generate() method."""
    __slots__ = ("mesh", "metadata", "solid")

    def __init__(self, mesh, metadata, solid=None):
        self.mesh = mesh          # rg.Mesh, oriented for printing (print-ready)
        self.metadata = metadata  # dict for .meta.json sidecar (algorithm-specific)
        self.solid = solid        # rg.Brep, final body (optional, for downstream inspection)


class SplintGenerator(object):
    """Base contract for Python-based splint generators.

    Subclasses set GEO_ALGORITHM_NAME and implement generate().
    """
    GEO_ALGORITHM_NAME = None

    def generate(self, raw_data, object_id, debug=None, stop_after=None):
        """Run the geometry pipeline and return a SplintResult.

        Args:
            raw_data: patient measurements dict (algorithm-specific payload).
            object_id: short ID string for embossing and tracing.
            debug: optional dict; when provided, the generator populates it with
                intermediate geometry for dev harness previewing via PhaseTracker.
            stop_after: optional float phase number; when set, the generator raises
                StopAfterPhase after completing that phase (for dev iteration).

        Returns:
            SplintResult with .mesh, .metadata, and .solid.

        Raises:
            StopAfterPhase if stop_after threshold is reached (not an error).
            Other exceptions on pipeline failure. The debug dict preserves
            intermediate state for diagnostics.
        """
        raise NotImplementedError
