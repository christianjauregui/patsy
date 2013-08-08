# This file is part of Patsy
# Copyright (C) 2012-2013 Nathaniel Smith <njs@pobox.com>
# See file COPYING for license information.

# R-compatible spline basis functions

import numpy as np

from patsy.state import stateful_transform

def _eval_bspline_basis(x, knots, degree):
    try:
        from scipy.interpolate import splev
    except ImportError:
        raise ImportError("spline functionality requires scipy")
    # 'knots' are assumed to be already pre-processed. E.g. usually you
    # want to include duplicate copies of boundary knots; you should do
    # that *before* calling this constructor.
    knots = np.atleast_1d(np.asarray(knots, dtype=float))
    if knots.ndim > 1:
        raise ValueError("knots must be 1 dimensional")
    knots.sort()
    degree = int(degree)
    x = np.atleast_1d(x)
    if x.ndim == 2 and x.shape[1] == 1:
        x = x[:, 0]
    if x.ndim > 1:
        raise ValueError("can't evaluate spline on multidim array")
    # XX FIXME: when points fall outside of the boundaries, splev and R seem
    # to handle them differently. I don't know why yet. So until we understand
    # this and decide what to do with it, I'm going to play it safe and
    # disallow such points.
    if np.min(x) < np.min(knots) or np.max(x) > np.max(knots):
        raise NotImplementedError("some data points fall outside the "
                                  "outermost knots, and I'm not sure how "
                                  "to handle them. (Patches accepted!)")
    # Thanks to Charles Harris for explaining splev. It's not well
    # documented, but basically it computes an arbitrary b-spline basis
    # given knots and degree on some specificed points (or derivatives
    # thereof, but we don't use that functionality), and then returns some
    # linear combination of these basis functions. To get out the basis
    # functions themselves, we use linear combinations like [1, 0, 0], [0,
    # 1, 0], [0, 0, 1].
    # NB: This probably makes it rather inefficient (though I haven't checked
    # to be sure -- maybe the fortran code actually skips computing the basis
    # function for coefficients that are zero).
    # Note: the order of a spline is the same as its degree + 1.
    # Note: there are (len(knots) - order) basis functions.
    n_bases = len(knots) - (degree + 1)
    basis = np.empty((x.shape[0], n_bases), dtype=float)
    for i in xrange(n_bases):
        coefs = np.zeros((n_bases,))
        coefs[i] = 1
        basis[:, i] = splev(x, (knots, coefs, degree))
    return basis

def _R_compat_quantile(x, probs):
    return np.percentile(x, 100 * np.asarray(probs))

def test__R_compat_quantile():
    def t(x, prob, expected):
        assert np.allclose(_R_compat_quantile(x, prob), expected)
    t([10, 20], 0.5, 15)
    t([10, 20], 0.3, 13)
    t([10, 20], [0.3, 0.7], [13, 17])
    t(range(10), [0.3, 0.7], [2.7, 6.3])

class BS(object):
    """bs(x, df=None, knots=None, degree=3, include_intercept=False, lower_bound=None, upper_bound=None)

    Generates B-spline bases useful for spline regression. The usual usage is
    something like::

      y ~ 1 + bs(x, 3)

    to fit `y` as a 3 degree-of-freedom smooth function of `x`.

    :arg df: The number of degrees of freedom to use for this spline. The
      return value will have this many columns. You must specify at least one
      of `df` and `knots`.
    :arg knots: The interior knots to use for the spline. If unspecified, then
      equally spaced quantiles of the input data are used. You must specify at
      least one of `df` and `knots`.
    :arg degree: The degree of the spline to use.
    :arg include_intercept: If `True`, then the resulting
      spline basis will span the intercept term (i.e., the constant
      function). If `False` (the default) then this will not be the case,
      which is useful for avoiding overspecification in models that include
      multiple spline terms and/or an intercept term.
    :arg lower_bound: The lower exterior knot location.
    :arg upper_bound: The upper exterior knot location.

    A degree 0 spline is piecewise constant with breakpoints at each knot, and
    the default knot positions are quantiles of the input. So if you find
    yourself in the situation of wanting to quantize a continuous variable
    into equal-sized bins with a constant effect across each bin, you can use
    `bs(x, num_bins, degree=0)`.

    .. warning:: I'm not sure on what the proper handling of points outside
      the lower/upper bounds is, so for now attempting to evaluate a spline
      basis at such points produces an error. Patches gratefully accepted.
    """
    def __init__(self):
        self._tmp = {}
        self._degree = None
        self._all_knots = None

    def memorize_chunk(self, x, df=None, knots=None, degree=3,
                       include_intercept=False,
                       lower_bound=None, upper_bound=None):
        args = {"df": df,
                "knots": knots,
                "degree": degree,
                "include_intercept": include_intercept,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
                }
        self._tmp["args"] = args
        # XX: check whether we need x values before saving them
        x = np.atleast_1d(x)
        if x.ndim > 1:
            raise ValueError("input to 'bs' must be 1-d vector")
        # There's no better way to compute exact quantiles than memorizing
        # all data.
        self._tmp.setdefault("xs", []).append(x)

    def memorize_finish(self):
        # These are guaranteed to all be 1d vectors by the code above
        tmp = self._tmp
        args = tmp["args"]
        del self._tmp
        x = np.concatenate(tmp["xs"])
        if args["df"] is None and args["knots"] is None:
            raise ValueError("must specify either df or knots")
        order = self._arg_degree + 1
        if args["df"] is not None:
            n_inner_knots = args["df"] - order
            if not args["include_intercept"]:
                n_inner_knots += 1
            if n_inner_knots < 0:
                raise ValueError("df=%r is too small for degree=%r and "
                                 "include_intercept=%r; must be >= %s"
                                 % (args["df"], args["degree"],
                                    args["include_intercept"],
                                    # We know that n_inner_knots is negative;
                                    # if df were that much larger, it would
                                    # have been zero, and things would work.
                                    args["df"] - n_inner_knots))
            if args["knots"] is not None:
                if len(args["knots"]) != n_inner_knots:
                    raise ValueError("df=%s implies %s knots, but %s "
                                     "knots were provided"
                                     % (args["df"], n_inner_knots,
                                        len(args["knots"])))
                else:
                    inner_knots = args["knots"]
            else:
                # Need to compute inner knots
                knot_quantiles = np.linspace(0, 1, n_inner_knots + 2)[1:-1]
                inner_knots = _R_compat_quantile(x, knot_quantiles)
        if args["lower_bound"] is not None:
            lower_bound = args["lower_bound"]
        else:
            lower_bound = np.min(x)
        if args["upper_bound"] is not None:
            upper_bound = args["upper_bound"]
        else:
            upper_bound = np.max(x)
        all_knots = np.concatenate(([lower_bound, upper_bound] * order,
                                    inner_knots))
        all_knots.sort()

        self._degree = degree
        self._all_knots = all_knots

    def transform(self, x, df=None, knots=None, degree=3,
                  include_intercept=False,
                  lower_bound=None, upper_bound=None):
        basis = _eval_bspline_basis(x, self._all_knots, self._degree)
        if not include_intercept:
            basis = basis[:, 1:]
        return basis

bs = stateful_transform(BS)

def test_bs_compat():
    from patsy.test_state import check_stateful
    from patsy.test_splines_bs_data import (R_bs_test_x,
                                            R_bs_test_data,
                                            R_bs_num_tests)
    lines = R_bs_test_data.split("\n")
    tests_ran = 0
    start_idx = lines.index("--BEGIN TEST CASE--")
    while True:
        if not lines[start_idx] == "--BEGIN TEST CASE--":
            break
        start_idx += 1
        stop_idx = lines.index("--END TEST CASE--", start_idx)
        block = lines[start_idx:stop_idx]
        test_data = {}
        for line in block:
            key, value = line.split("=")
            test_data[key] = value
        # Translate the R output into Python calling conventions
        kwargs = {
            "degree": int(test_data["degree"]),
            "df": eval(test_data["df"]),
            "knots": eval(test_data["knots"]),
            }
        # Special case: R doesn't distinguish between [] and None. So the case
        # where df=NULL, knots=NULL is actually interpreted like df=None,
        # knots=[].
        if kwargs["df"] is None and kwargs["knots"] is None:
            kwargs["knots"] = []
        if data["Boundary.knots"] != "None":
            lower, upper = eval(test_data["Boundary.knots"])
            kwargs["lower_bound"] = lower
            kwargs["upper_bound"] = upper
        kwargs["include_intercept"] = (data["intercept"] == "TRUE")
        # Do the actual test
        check_stateful(BS, R_bs_test_x, **kwargs)
        tests_ran += 1
        # Set up for the next one
        start_idx = stop_idx + 1
    assert tests_ran == R_bs_num_tests

# Test x vector is (1.5 ** arange(20))
# Try with all combinations of:
#   degree 1, 3, 5
#     specified knots, df = 3, 5, 10
#   include_intercept True/False
#   upper_bound = (1.5 ** 19), (1.5 ** 20)
#   lower_bound = (1.5 ** 0) == 1, 0
# check:
#   df = result.shape[1]
#   matches R
# combined or broken input
#
# special case in reading this: df=None and knots=None should translate to
# df=None, knots=[]
#
# error checks:
#   out of bounds
#   df/knots mismatch (with and without intercept)
#   negative degree
#   upper_bound < lower_bound
#   knots > upper_bound
#   knots < lower_bound
#
# one off checks:
#   degree 0 (R doesn't support this)

# ns:
# - degree is always 3
# - different number of interior knots given df (b/c fewer dof used at edges I
#   guess)
# - boundary knots always repeated exactly 4 times (same as bs with degree=3)
# - complications at the end to
