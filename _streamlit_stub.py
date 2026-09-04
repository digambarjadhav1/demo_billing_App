"""
Minimal stand-in for the `streamlit` module, used ONLY to smoke-test app.py's
logic (widget wiring, DB queries, formatting) in an environment with no
network access to actually `pip install streamlit`. This is not a real UI and
must not be mistaken for one — install the real package and run
`streamlit run app.py` to verify the actual UI before treating this app as done.
"""
import os
import sys
import types


class _Sidebar:
    def title(self, *a, **k): print(f"[sidebar.title] {a}")
    def selectbox(self, label, options, *a, **k):
        options = list(options)
        if label == "Project" and os.environ.get("STUB_PROJECT_CHOICE"):
            forced = os.environ["STUB_PROJECT_CHOICE"]
            matches = [o for o in options if o.startswith(forced)]
            choice = matches[0] if matches else (options[0] if options else None)
        elif label == "Acting as role" and os.environ.get("STUB_ROLE_CHOICE"):
            forced = os.environ["STUB_ROLE_CHOICE"]
            choice = forced if forced in options else (options[0] if options else None)
        else:
            choice = options[0] if options else None
        print(f"[sidebar.selectbox] {label} -> {choice!r}")
        return choice
    def text_input(self, label, value="", *a, **k):
        print(f"[sidebar.text_input] {label} -> {value!r}")
        return value
    def radio(self, label, options, *a, **k):
        forced = os.environ.get("STUB_SCREEN_CHOICE")
        choice = forced if forced in options else options[0]
        print(f"[sidebar.radio] {label} -> {choice}")
        return choice


class _Column:
    def metric(self, label, value, *a, **k):
        print(f"    [metric] {label}: {value}")
    def button(self, label, *a, **k):
        print(f"    [button] {label} -> False (stub, not clicked)")
        return False


class _Expander:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _make_module():
    m = types.ModuleType("streamlit")
    m.sidebar = _Sidebar()

    def set_page_config(*a, **k): print(f"[set_page_config] {k}")
    def cache_resource(fn=None, **k):
        if fn is None:
            return lambda f: f
        return fn
    def header(text, *a, **k): print(f"\n===== {text} =====")
    def subheader(text, *a, **k): print(f"--- {text} ---")
    def caption(text, *a, **k): print(f"(caption) {text}")
    def metric(label, value, *a, **k): print(f"[metric] {label}: {value}")
    def columns(n, *a, **k): return [_Column() for _ in range(n)]
    def table(data, *a, **k): print(f"[table] {len(data)} rows -> {data[:2]}{'...' if len(data) > 2 else ''}")
    def dataframe(data, *a, **k): print(f"[dataframe] {len(data)} rows -> {data[:2]}{'...' if len(data) > 2 else ''}")
    def line_chart(data, *a, **k):
        # Real st.line_chart rejects dict-of-dict input; enforce the same shape
        # here so this stub actually catches that class of bug.
        if isinstance(data, dict):
            for v in data.values():
                assert not isinstance(v, dict), (
                    "st.line_chart does not accept a dict of dicts — pass a "
                    "pandas DataFrame or a flat dict instead."
                )
            print(f"[line_chart] flat dict, keys: {list(data.keys())[:5]}")
        else:
            print(f"[line_chart] DataFrame-like, shape={getattr(data, 'shape', '?')}, "
                  f"columns={list(getattr(data, 'columns', []))}")
    def bar_chart(data, *a, **k): print(f"[bar_chart] {len(data)} points")
    def markdown(text, *a, **k): print(f"(markdown) {text}")
    def info(text, *a, **k): print(f"[info] {text}")
    def success(text, *a, **k): print(f"[success] {text}")
    def warning(text, *a, **k): print(f"[warning] {text}")
    def error(text, *a, **k): print(f"[error] {text}")
    def stop(*a, **k):
        print("[stop] app.stop() called")
        raise SystemExit(0)
    def rerun(*a, **k): print("[rerun] (stub: no-op)")
    def button(label, *a, **k):
        print(f"[button] {label} -> False (stub, not clicked)")
        return False
    def selectbox(label, options, *a, format_func=None, **k):
        options = list(options)
        choice = options[0] if options else None
        shown = format_func(choice) if format_func and choice is not None else choice
        print(f"[selectbox] {label} -> {shown!r}")
        return choice
    def text_area(label, *a, **k):
        print(f"[text_area] {label} -> ''")
        return ""
    def number_input(label, *a, value=0.0, **k):
        print(f"[number_input] {label} -> {value}")
        return value
    def slider(label, minv, maxv, *a, **k):
        print(f"[slider] {label} -> {minv}")
        return minv
    def expander(label, *a, **k):
        print(f"[expander] {label}")
        return _Expander()
    def checkbox(label, *a, value=False, **k):
        print(f"[checkbox] {label} -> {value}")
        return value
    def date_input(label, *a, **k):
        import datetime as _dt
        print(f"[date_input] {label} -> today")
        return _dt.date.today()
    def text_input(label, *a, value="", **k):
        print(f"[text_input] {label} -> {value!r}")
        return value
    def form(key, *a, **k):
        print(f"[form] {key}")
        return _Expander()
    def form_submit_button(label, *a, **k):
        print(f"[form_submit_button] {label} -> False (stub, not clicked)")
        return False

    for name, fn in list(locals().items()):
        if callable(fn) and not name.startswith("_"):
            setattr(m, name, fn)
    return m


sys.modules["streamlit"] = _make_module()
