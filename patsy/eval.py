# This file is part of Patsy
# Copyright (C) 2011 Nathaniel Smith <njs@pobox.com>
# See file COPYING for license information.

# Utilities that require an over-intimate knowledge of Python's execution
# environment.

# These are made available in the patsy.* namespace
__all__ = ["EvalEnvironment", "EvalFactor"]

import sys
import __future__
import inspect
import tokenize
from patsy import PatsyError
from patsy.util import PushbackAdapter
from patsy.tokens import (pretty_untokenize, normalize_token_spacing,
                             python_tokenize)

def _all_future_flags():
    flags = 0
    for feature_name in __future__.all_feature_names:
        feature = getattr(__future__, feature_name)
        if feature.getMandatoryRelease() > sys.version_info:
            flags |= feature.compiler_flag
    return flags

_ALL_FUTURE_FLAGS = _all_future_flags()

# This is just a minimal dict-like object that does lookup in a 'stack' of
# dicts -- first it checks the first, then the second, etc. Assignments go
# into an internal, zeroth dict.
class VarLookupDict(object):
    def __init__(self, dicts):
        self._dicts = [{}] + list(dicts)

    def __getitem__(self, key):
        for d in self._dicts:
            try:
                return d[key]
            except KeyError:
                pass
        raise KeyError, key

    def __setitem__(self, key, value):
        self._dicts[0][key] = value

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        else:
            return True

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self._dicts)

def test_VarLookupDict():
    d1 = {"a": 1}
    d2 = {"a": 2, "b": 3}
    ds = VarLookupDict([d1, d2])
    assert ds["a"] == 1
    assert ds["b"] == 3
    assert "a" in ds
    assert "c" not in ds
    from nose.tools import assert_raises
    assert_raises(KeyError, ds.__getitem__, "c")
    ds["a"] = 10
    assert ds["a"] == 10
    assert d1["a"] == 1

class EvalEnvironment(object):
    """Represents a Python execution environment.

    Encapsulates a namespace for variable lookup and set of __future__
    flags."""
    def __init__(self, namespaces, flags=0):
        assert not flags & ~_ALL_FUTURE_FLAGS
        self._namespaces = list(namespaces)
        self.flags = flags

    @property
    def namespace(self):
        """A dict-like object that can be used to look up variables accessible
        from the encapsulated environment."""
        return VarLookupDict(self._namespaces)

    def add_outer_namespace(self, namespace):
        """Expose the contents of a dict-like object to the encapsulated
        environment.

        The given namespace will be checked last, after all existing namespace
        lookups have failed.
        """
        # ModelDesc.from_formula unconditionally calls
        #   eval_env.add_outer_namespace(builtins)
        # which means that if someone uses the same environment for a bunch of
        # formulas, our namespace chain will grow without bound, which would
        # suck.
        if id(namespace) not in self._namespace_ids():
            self._namespaces.append(namespace)

    def eval(self, expr, source_name="<string>", inner_namespace={}):
        """Evaluate some Python code in the encapsulated environment.

        :arg expr: A string containing a Python expression.
        :arg source_name: A name for this string, for use in tracebacks.
        :arg inner_namespace: A dict-like object that will be checked first
          when `expr` attempts to access any variables.
        :returns: The value of `expr`.
        """
        code = compile(expr, source_name, "eval", self.flags, False)
        return eval(code, {}, VarLookupDict([inner_namespace]
                                            + self._namespaces))

    @classmethod
    def capture(cls, depth=0):
        """Capture an execution environment from the stack.

        The optional argument `depth` specifies which stack frame to
        capture. ``depth=0`` (the default) captures the stack frame of the
        function that calls :meth:`capture`. ``depth=1`` captures that
        functions caller, and so forth.

        Example::

          x = 1
          this_env = EvalEnvironment.capture()
          assert this_env["x"] == 1
          def child_func():
              return EvalEnvironment.capture(1)
          this_env_from_child = child_func()
          assert this_env_from_child["x"] == 1
        """
        frame = inspect.currentframe()
        try:
            for i in xrange(depth + 1):
                if frame is None:
                    raise ValueError, "call-stack is not that deep!"
                frame = frame.f_back
            return cls([frame.f_locals, frame.f_globals],
                       frame.f_code.co_flags & _ALL_FUTURE_FLAGS)
        # The try/finally is important to avoid a potential reference cycle --
        # any exception traceback will carry a reference to *our* frame, which
        # contains a reference to our local variables, which would otherwise
        # carry a reference to some parent frame, where the exception was
        # caught...:
        finally:
            del frame

    def _namespace_ids(self):
        return [id(n) for n in self._namespaces]

    def __eq__(self, other):
        return (isinstance(other, EvalEnvironment)
                and self.flags == other.flags
                and self._namespace_ids() == other._namespace_ids())

    def __hash__(self):
        return hash((EvalEnvironment,
                     self.flags,
                     tuple(self._namespace_ids())))

def _a(): # pragma: no cover
    _a = 1
    return _b()

def _b(): # pragma: no cover
    _b = 1
    return _c()

def _c(): # pragma: no cover
    _c = 1
    return [EvalEnvironment.capture(),
            EvalEnvironment.capture(0),
            EvalEnvironment.capture(1),
            EvalEnvironment.capture(2),
            ]

def test_EvalEnvironment_capture_namespace():
    c0, c, b, a = _a()
    assert "test_EvalEnvironment_capture_namespace" in c0.namespace
    assert "test_EvalEnvironment_capture_namespace" in c.namespace
    assert "test_EvalEnvironment_capture_namespace" in b.namespace
    assert "test_EvalEnvironment_capture_namespace" in a.namespace
    assert c0.namespace["_c"] == 1
    assert c.namespace["_c"] == 1
    assert b.namespace["_b"] == 1
    assert a.namespace["_a"] == 1
    assert b.namespace["_c"] is _c
    from nose.tools import assert_raises
    assert_raises(ValueError, EvalEnvironment.capture, 10 ** 6)

def test_EvalEnvironment_capture_flags():
    if sys.version_info >= (3,):
        # This is the only __future__ feature currently usable in Python
        # 3... fortunately it is probably not going anywhere.
        TEST_FEATURE = "barry_as_FLUFL"
    else:
        TEST_FEATURE = "division"
    test_flag = getattr(__future__, TEST_FEATURE).compiler_flag
    assert test_flag & _ALL_FUTURE_FLAGS
    source = ("def f():\n"
              "    in_f = 'hi from f'\n"
              "    global RETURN_INNER, RETURN_OUTER, RETURN_INNER_FROM_OUTER\n"
              "    RETURN_INNER = EvalEnvironment.capture(0)\n"
              "    RETURN_OUTER = call_capture_0()\n"
              "    RETURN_INNER_FROM_OUTER = call_capture_1()\n"
              "f()\n")
    code = compile(source, "<test string>", "exec", 0, 1)
    env = {"EvalEnvironment": EvalEnvironment,
           "call_capture_0": lambda: EvalEnvironment.capture(0),
           "call_capture_1": lambda: EvalEnvironment.capture(1),
           }
    env2 = dict(env)
    exec code in env
    assert env["RETURN_INNER"].namespace["in_f"] == "hi from f"
    assert env["RETURN_INNER_FROM_OUTER"].namespace["in_f"] == "hi from f"
    assert "in_f" not in env["RETURN_OUTER"].namespace
    assert env["RETURN_INNER"].flags & _ALL_FUTURE_FLAGS == 0
    assert env["RETURN_OUTER"].flags & _ALL_FUTURE_FLAGS == 0
    assert env["RETURN_INNER_FROM_OUTER"].flags & _ALL_FUTURE_FLAGS == 0

    code2 = compile(("from __future__ import %s\n" % (TEST_FEATURE,))
                    + source,
                    "<test string 2>", "exec", 0, 1)
    exec code2 in env2
    assert env2["RETURN_INNER"].namespace["in_f"] == "hi from f"
    assert env2["RETURN_INNER_FROM_OUTER"].namespace["in_f"] == "hi from f"
    assert "in_f" not in env2["RETURN_OUTER"].namespace
    assert env2["RETURN_INNER"].flags & _ALL_FUTURE_FLAGS == test_flag
    assert env2["RETURN_OUTER"].flags & _ALL_FUTURE_FLAGS == 0
    assert env2["RETURN_INNER_FROM_OUTER"].flags & _ALL_FUTURE_FLAGS == test_flag

def test_EvalEnvironment_eval_namespace():
    env = EvalEnvironment([{"a": 1}])
    assert env.eval("2 * a") == 2
    assert env.eval("2 * a", inner_namespace={"a": 2}) == 4
    from nose.tools import assert_raises
    assert_raises(NameError, env.eval, "2 * b")
    a = 3
    env2 = EvalEnvironment.capture(0)
    assert env2.eval("2 * a") == 6

def test_EvalEnvironment_eval_flags():
    from nose.tools import assert_raises
    if sys.version_info >= (3,):
        # This joke __future__ statement replaces "!=" with "<>":
        #   http://www.python.org/dev/peps/pep-0401/
        test_flag = __future__.barry_as_FLUFL.compiler_flag
        assert test_flag & _ALL_FUTURE_FLAGS
        env = EvalEnvironment([{"a": 11}], flags=0)
        assert env.eval("a != 0") == True
        assert_raises(SyntaxError, env.eval, "a <> 0")
        env2 = EvalEnvironment([{"a": 11}], flags=test_flag)
        assert env2.eval("a <> 0") == True
        assert_raises(SyntaxError, env2.eval, "a != 0")
    else:
        test_flag = __future__.division.compiler_flag
        assert test_flag & _ALL_FUTURE_FLAGS
        env = EvalEnvironment([{"a": 11}], flags=0)
        assert env.eval("a / 2") == 11 // 2 == 5
        env2 = EvalEnvironment([{"a": 11}], flags=test_flag)
        assert env2.eval("a / 2") == 11 * 1. / 2 != 5

def test_EvalEnvironment_eq():
    # Two environments are eq only if they refer to exactly the same
    # global/local dicts
    env1 = EvalEnvironment.capture(0)
    env2 = EvalEnvironment.capture(0)
    assert env1 == env2
    assert hash(env1) == hash(env2)
    capture_local_env = lambda: EvalEnvironment.capture(0)
    env3 = capture_local_env()
    env4 = capture_local_env()
    assert env3 != env4

def test_EvalEnvironment_add_outer_namespace():
    a = 1
    env = EvalEnvironment.capture(0)
    env2 = EvalEnvironment.capture(0)
    assert env.namespace["a"] == 1
    assert "b" not in env.namespace
    assert env == env2
    env.add_outer_namespace({"a": 10, "b": 2})
    assert env.namespace["a"] == 1
    assert env.namespace["b"] == 2
    assert env != env2

class EvalFactor(object):
    def __init__(self, code, eval_env, origin=None):
        """A factor class that executes arbitrary Python code and supports
        stateful transforms.

        :arg code: A string containing a Python expression, that will be
          evaluated to produce this factor's value.
        :arg eval_env: The class:`EvalEnvironment` where `code` will be
          evaluated.

        This is the standard factor class that is used when parsing formula
        strings and implements the standard stateful transform processing. See
        :ref:`stateful-transforms` and :ref:`expert-model-specification`.

        Two EvalFactor's are considered equal (e.g., for purposes of
        redundancy detection) if they use the same evaluation environment and
        they contain the same token stream. Basically this means that the
        source code must be identical except for whitespace::

          env = EvalEnvironment.capture()
          assert EvalFactor("a + b", env) == EvalFactor("a+b", env)
          assert EvalFactor("a + b", env) != EvalFactor("b + a", env)
        """
        # For parsed formulas, the code will already have been normalized by
        # the parser. But let's normalize anyway, so we can be sure of having
        # consistent semantics for __eq__ and __hash__.
        self.code = normalize_token_spacing(code)
        self._eval_env = eval_env
        self.origin = origin

    def name(self):
        return self.code

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self.code)

    def __eq__(self, other):
        return (isinstance(other, EvalFactor)
                and self.code == other.code
                and self._eval_env == other._eval_env)

    def __hash__(self):
        return hash((EvalFactor, self.code, self._eval_env))

    def memorize_passes_needed(self, state):
        # 'state' is just an empty dict which we can do whatever we want with,
        # and that will be passed back to later memorize functions
        state["transforms"] = {}

        # example code: == "2 * center(x)"
        i = [0]
        def new_name_maker(token):
            value = self._eval_env.namespace.get(token)
            if hasattr(value, "__patsy_stateful_transform__"):
                obj_name = "_patsy_stobj%s__%s__" % (i[0], token)
                i[0] += 1
                obj = value.__patsy_stateful_transform__()
                state["transforms"][obj_name] = obj
                return obj_name + ".transform"
            else:
                return token
        # example eval_code: == "2 * _patsy_stobj0__center__.transform(x)"
        eval_code = replace_bare_funcalls(self.code, new_name_maker)
        state["eval_code"] = eval_code
        # paranoia: verify that none of our new names appeared anywhere in the
        # original code
        if has_bare_variable_reference(state["transforms"], self.code):
            raise PatsyError("names of this form are reserved for "
                                "internal use (%s)" % (token,), token.origin)
        # Pull out all the '_patsy_stobj0__center__.transform(x)' pieces
        # to make '_patsy_stobj0__center__.memorize_chunk(x)' pieces
        state["memorize_code"] = {}
        for obj_name in state["transforms"]:
            transform_calls = capture_obj_method_calls(obj_name, eval_code)
            assert len(transform_calls) == 1
            transform_call = transform_calls[0]
            transform_call_name, transform_call_code = transform_call
            assert transform_call_name == obj_name + ".transform"
            assert transform_call_code.startswith(transform_call_name + "(")
            memorize_code = (obj_name
                             + ".memorize_chunk"
                             + transform_call_code[len(transform_call_name):])
            state["memorize_code"][obj_name] = memorize_code
        # Then sort the codes into bins, so that every item in bin number i
        # depends only on items in bin (i-1) or less. (By 'depends', we mean
        # that in something like:
        #   spline(center(x))
        # we have to first run:
        #    center.memorize_chunk(x)
        # then
        #    center.memorize_finish(x)
        # and only then can we run:
        #    spline.memorize_chunk(center.transform(x))
        # Since all of our objects have unique names, figuring out who
        # depends on who is pretty easy -- we just check whether the
        # memorization code for spline:
        #    spline.memorize_chunk(center.transform(x))
        # mentions the variable 'center' (which in the example, of course, it
        # does).
        pass_bins = []
        unsorted = set(state["transforms"])
        while unsorted:
            pass_bin = set()
            for obj_name in unsorted:
                other_objs = unsorted.difference([obj_name])
                memorize_code = state["memorize_code"][obj_name]
                if not has_bare_variable_reference(other_objs, memorize_code):
                    pass_bin.add(obj_name)
            assert pass_bin
            unsorted.difference_update(pass_bin)
            pass_bins.append(pass_bin)
        state["pass_bins"] = pass_bins

        return len(pass_bins)

    def _eval(self, code, memorize_state, data):
        inner_namespace = VarLookupDict([data, memorize_state["transforms"]])
        return self._eval_env.eval(code, inner_namespace=inner_namespace)

    def memorize_chunk(self, state, which_pass, data):
        for obj_name in state["pass_bins"][which_pass]:
            self._eval(state["memorize_code"][obj_name], state, data)

    def memorize_finish(self, state, which_pass):
        for obj_name in state["pass_bins"][which_pass]:
            state["transforms"][obj_name].memorize_finish()

    # XX FIXME: consider doing something cleverer with exceptions raised here,
    # to make it clearer what's really going on. The new exception chaining
    # stuff doesn't appear to be present in any 2.x version of Python, so we
    # can't use that, but some other options:
    #    http://blog.ianbicking.org/2007/09/12/re-raising-exceptions/
    #    http://nedbatchelder.com/blog/200711/rethrowing_exceptions_in_python.html
    def eval(self, memorize_state, data):
        return self._eval(memorize_state["eval_code"], memorize_state, data)

def test_EvalFactor_basics():
    e = EvalFactor("a+b", EvalEnvironment.capture(0))
    assert e.code == "a + b"
    assert e.name() == "a + b"
    e2 = EvalFactor("a    +b", EvalEnvironment.capture(0), origin="asdf")
    assert e == e2
    assert hash(e) == hash(e2)
    assert e.origin is None
    assert e2.origin == "asdf"

def test_EvalFactor_memorize_passes_needed():
    from patsy.state import stateful_transform
    foo = stateful_transform(lambda: "FOO-OBJ")
    bar = stateful_transform(lambda: "BAR-OBJ")
    quux = stateful_transform(lambda: "QUUX-OBJ")
    e = EvalFactor("foo(x) + bar(foo(y)) + quux(z, w)",
                   EvalEnvironment.capture(0))
    state = {}
    passes = e.memorize_passes_needed(state)
    print passes
    print state
    assert passes == 2
    assert state["transforms"] == {"_patsy_stobj0__foo__": "FOO-OBJ",
                                   "_patsy_stobj1__bar__": "BAR-OBJ",
                                   "_patsy_stobj2__foo__": "FOO-OBJ",
                                   "_patsy_stobj3__quux__": "QUUX-OBJ"}
    assert (state["eval_code"]
            == "_patsy_stobj0__foo__.transform(x)"
               " + _patsy_stobj1__bar__.transform("
               "_patsy_stobj2__foo__.transform(y))"
               " + _patsy_stobj3__quux__.transform(z, w)")

    assert (state["memorize_code"]
            == {"_patsy_stobj0__foo__":
                    "_patsy_stobj0__foo__.memorize_chunk(x)",
                "_patsy_stobj1__bar__":
                    "_patsy_stobj1__bar__.memorize_chunk(_patsy_stobj2__foo__.transform(y))",
                "_patsy_stobj2__foo__":
                    "_patsy_stobj2__foo__.memorize_chunk(y)",
                "_patsy_stobj3__quux__":
                    "_patsy_stobj3__quux__.memorize_chunk(z, w)",
                })
    assert state["pass_bins"] == [set(["_patsy_stobj0__foo__",
                                       "_patsy_stobj2__foo__",
                                       "_patsy_stobj3__quux__"]),
                                  set(["_patsy_stobj1__bar__"])]

class _MockTransform(object):
    # Adds up all memorized data, then subtracts that sum from each datum
    def __init__(self):
        self._sum = 0
        self._memorize_chunk_called = 0
        self._memorize_finish_called = 0

    def memorize_chunk(self, data):
        self._memorize_chunk_called += 1
        import numpy as np
        self._sum += np.sum(data)

    def memorize_finish(self):
        self._memorize_finish_called += 1

    def transform(self, data):
        return data - self._sum

def test_EvalFactor_end_to_end():
    from patsy.state import stateful_transform
    foo = stateful_transform(_MockTransform)
    e = EvalFactor("foo(x) + foo(foo(y))", EvalEnvironment.capture(0))
    state = {}
    passes = e.memorize_passes_needed(state)
    print passes
    print state
    assert passes == 2
    import numpy as np
    e.memorize_chunk(state, 0,
                     {"x": np.array([1, 2]),
                      "y": np.array([10, 11])})
    assert state["transforms"]["_patsy_stobj0__foo__"]._memorize_chunk_called == 1
    assert state["transforms"]["_patsy_stobj2__foo__"]._memorize_chunk_called == 1
    e.memorize_chunk(state, 0, {"x": np.array([12, -10]),
                                "y": np.array([100, 3])})
    assert state["transforms"]["_patsy_stobj0__foo__"]._memorize_chunk_called == 2
    assert state["transforms"]["_patsy_stobj2__foo__"]._memorize_chunk_called == 2
    assert state["transforms"]["_patsy_stobj0__foo__"]._memorize_finish_called == 0
    assert state["transforms"]["_patsy_stobj2__foo__"]._memorize_finish_called == 0
    e.memorize_finish(state, 0)
    assert state["transforms"]["_patsy_stobj0__foo__"]._memorize_finish_called == 1
    assert state["transforms"]["_patsy_stobj2__foo__"]._memorize_finish_called == 1
    assert state["transforms"]["_patsy_stobj1__foo__"]._memorize_chunk_called == 0
    assert state["transforms"]["_patsy_stobj1__foo__"]._memorize_finish_called == 0
    e.memorize_chunk(state, 1, {"x": np.array([1, 2]),
                                "y": np.array([10, 11])})
    e.memorize_chunk(state, 1, {"x": np.array([12, -10]),
                                "y": np.array([100, 3])})
    e.memorize_finish(state, 1)
    for transform in state["transforms"].itervalues():
        assert transform._memorize_chunk_called == 2
        assert transform._memorize_finish_called == 1
    # sums:
    # 0: 1 + 2 + 12 + -10 == 5
    # 2: 10 + 11 + 100 + 3 == 124
    # 1: (10 - 124) + (11 - 124) + (100 - 124) + (3 - 124) == -372
    # results:
    # 0: -4, -3, 7, -15
    # 2: -114, -113, -24, -121
    # 1: 258, 259, 348, 251
    # 0 + 1: 254, 256, 355, 236
    assert np.all(e.eval(state,
                         {"x": np.array([1, 2, 12, -10]),
                          "y": np.array([10, 11, 100, 3])})
                  == [254, 256, 355, 236])

def annotated_tokens(code):
    prev_was_dot = False
    it = PushbackAdapter(python_tokenize(code))
    for (token_type, token, origin) in it:
        props = {}
        props["bare_ref"] = (not prev_was_dot and token_type == tokenize.NAME)
        props["bare_funcall"] = (props["bare_ref"]
                                 and it.has_more() and it.peek()[1] == "(")
        yield (token_type, token, origin, props)
        prev_was_dot = (token == ".")

def test_annotated_tokens():
    tokens_without_origins = [(token_type, token, props)
                              for (token_type, token, origin, props)
                              in (annotated_tokens("a(b) + c.d"))]
    assert (tokens_without_origins
            == [(tokenize.NAME, "a", {"bare_ref": True, "bare_funcall": True}),
                (tokenize.OP, "(", {"bare_ref": False, "bare_funcall": False}),
                (tokenize.NAME, "b", {"bare_ref": True, "bare_funcall": False}),
                (tokenize.OP, ")", {"bare_ref": False, "bare_funcall": False}),
                (tokenize.OP, "+", {"bare_ref": False, "bare_funcall": False}),
                (tokenize.NAME, "c", {"bare_ref": True, "bare_funcall": False}),
                (tokenize.OP, ".", {"bare_ref": False, "bare_funcall": False}),
                (tokenize.NAME, "d",
                    {"bare_ref": False, "bare_funcall": False}),
                ])

    # This was a bug:
    assert len(list(annotated_tokens("x"))) == 1

def has_bare_variable_reference(names, code):
    for (_, token, _, props) in annotated_tokens(code):
        if props["bare_ref"] and token in names:
            return True
    return False

def replace_bare_funcalls(code, replacer):
    tokens = []
    for (token_type, token, origin, props) in annotated_tokens(code):
        if props["bare_ref"] and props["bare_funcall"]:
            token = replacer(token)
        tokens.append((token_type, token))
    return pretty_untokenize(tokens)

def test_replace_bare_funcalls():
    def replacer1(token):
        return {"a": "b", "foo": "_internal.foo.process"}.get(token, token)
    def t1(code, expected):
        replaced = replace_bare_funcalls(code, replacer1)
        print "%r -> %r" % (code, replaced)
        print "(wanted %r)" % (expected,)
        assert replaced == expected
    t1("foobar()", "foobar()")
    t1("a()", "b()")
    t1("foobar.a()", "foobar.a()")
    t1("foo()", "_internal.foo.process()")
    t1("a + 1", "a + 1")
    t1("b() + a() * x[foo(2 ** 3)]",
       "b() + b() * x[_internal.foo.process(2 ** 3)]")

class _FuncallCapturer(object):
    # captures the next funcall
    def __init__(self, start_token_type, start_token):
        self.func = [start_token]
        self.tokens = [(start_token_type, start_token)]
        self.paren_depth = 0
        self.started = False
        self.done = False

    def add_token(self, token_type, token):
        if self.done:
            return
        self.tokens.append((token_type, token))
        if token in ["(", "{", "["]:
            self.paren_depth += 1
        if token in [")", "}", "]"]:
            self.paren_depth -= 1
        assert self.paren_depth >= 0
        if not self.started:
            if token == "(":
                self.started = True
            else:
                assert token_type == tokenize.NAME or token == "."
                self.func.append(token)
        if self.started and self.paren_depth == 0:
            self.done = True

# This is not a very general function -- it assumes that all references to the
# given object are of the form '<obj_name>.something(method call)'.
def capture_obj_method_calls(obj_name, code):
    capturers = []
    for (token_type, token, origin, props) in annotated_tokens(code):
        for capturer in capturers:
            capturer.add_token(token_type, token)
        if props["bare_ref"] and token == obj_name:
            capturers.append(_FuncallCapturer(token_type, token))
    return [("".join(capturer.func), pretty_untokenize(capturer.tokens))
            for capturer in capturers]

def test_capture_obj_method_calls():
    assert (capture_obj_method_calls("foo", "a + foo.baz(bar) + b.c(d)")
            == [("foo.baz", "foo.baz(bar)")])
    assert (capture_obj_method_calls("b", "a + foo.baz(bar) + b.c(d)")
            == [("b.c", "b.c(d)")])
    assert (capture_obj_method_calls("foo", "foo.bar(foo.baz(quux))")
            == [("foo.bar", "foo.bar(foo.baz(quux))"),
                ("foo.baz", "foo.baz(quux)")])
    assert (capture_obj_method_calls("bar", "foo[bar.baz(x(z[asdf])) ** 2]")
            == [("bar.baz", "bar.baz(x(z[asdf]))")])
