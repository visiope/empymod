"""

:mod:`transform` -- Hankel and Fourier Transforms
=================================================

Methods to carry out the required Hankel transform from wavenumber to
frequency domain and Fourier transform from frequency to time domain.

The functions for the QWE and DLF Hankel and Fourier transforms are based on
source files (specified in each function) from the source code distributed with
[Key_2012]_, which can be found at `software.seg.org/2012/0003
<http://software.seg.org/2012/0003>`_. These functions are (c) 2012 by Kerry
Key and the Society of Exploration Geophysicists,
http://software.seg.org/disclaimer.txt. Please read the NOTICE-file in the root
directory for more information regarding the involved licenses.

"""
# Copyright 2016-2018 Dieter Werthmüller
#
# This file is part of empymod.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations under
# the License.


import numpy as np
from scipy import special, fftpack, integrate
from scipy.interpolate import InterpolatedUnivariateSpline as iuSpline

from . import kernel

__all__ = ['fht', 'hqwe', 'hquad', 'ffht', 'fqwe', 'fftlog', 'fft', 'dlf',
           'qwe', 'get_spline_values', 'fhti']


# 1. Hankel transforms (wavenumber -> frequency)

def fht(zsrc, zrec, lsrc, lrec, off, angle, depth, ab, etaH, etaV, zetaH,
        zetaV, xdirect, fhtarg, use_ne_eval, msrc, mrec):
    """Hankel Transform using the Digital Linear Filter method.

    The *Digital Linear Filter* method was introduced to geophysics by
    [Ghosh_1971]_, and made popular and wide-spread by [Anderson_1975]_,
    [Anderson_1979]_, [Anderson_1982]_. The DLF is sometimes referred to as
    the *Fast Hankel Transform* FHT, from which this routine has its name.

    This implementation of the DLF follows [Key_2012]_, equation 6.  Without
    going into the mathematical details (which can be found in any of the above
    papers) and following [Key_2012]_, the DLF method rewrites the Hankel
    transform of the form

    .. math:: F(r)   = \int^\infty_0 f(\lambda)J_v(\lambda r)\
            \mathrm{d}\lambda

    as

    .. math::   F(r)   = \sum^n_{i=1} f(b_i/r)h_i/r \ ,

    where :math:`h` is the digital filter.The Filter abscissae b is given by

    .. math:: b_i = \lambda_ir = e^{ai}, \qquad i = -l, -l+1, \cdots, l \ ,

    with :math:`l=(n-1)/2`, and :math:`a` is the spacing coefficient.

    This function is loosely based on ``get_CSEM1D_FD_FHT.m`` from the source
    code distributed with [Key_2012]_.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    Returns
    -------
    fEM : array
        Returns frequency-domain EM response.

    kcount : int
        Kernel count. For DLF, this is 1.

    conv : bool
        Only relevant for QWE/QUAD.

    """
    # Get fhtargs
    fhtfilt = fhtarg[0]
    pts_per_dec = fhtarg[1]

    # 1. Compute required lambdas for given hankel-filter-base
    lambd, _ = get_spline_values(fhtfilt, off, pts_per_dec)

    # 2. Call the kernel
    PJ = kernel.wavenumber(zsrc, zrec, lsrc, lrec, depth, etaH, etaV, zetaH,
                           zetaV, lambd, ab, xdirect, msrc, mrec, use_ne_eval)

    # 3. Angle dependent factors
    factAng = kernel.angle_factor(angle, ab, msrc, mrec)

    # 4. Carry out the dlf
    fEM = dlf(PJ, lambd, off, fhtfilt, pts_per_dec, factAng=factAng, ab=ab)

    return fEM, 1, True


def hqwe(zsrc, zrec, lsrc, lrec, off, angle, depth, ab, etaH, etaV, zetaH,
         zetaV, xdirect, qweargs, use_ne_eval, msrc, mrec):
    """Hankel Transform using Quadrature-With-Extrapolation.

    *Quadrature-With-Extrapolation* was introduced to geophysics by
    [Key_2012]_. It is one of many so-called *ISE* methods to solve Hankel
    Transforms, where *ISE* stands for Integration, Summation, and
    Extrapolation.

    Following [Key_2012]_, but without going into the mathematical details
    here, the QWE method rewrites the Hankel transform of the form

    .. math:: F(r)   = \int^\infty_0 f(\lambda)J_v(\lambda r)\
            \mathrm{d}\lambda

    as a quadrature sum which form is similar to the DLF (equation 15),

    .. math::   F_i   \\approx \sum^m_{j=1} f(x_j/r)w_j g(x_j) =
                \sum^m_{j=1} f(x_j/r)\hat{g}(x_j) \ ,

    but with various bells and whistles applied (using the so-called Shanks
    transformation in the form of a routine called :math:`\epsilon`-algorithm
    ([Shanks_1955]_, [Wynn_1956]_; implemented with algorithms from
    [Trefethen_2000]_ and [Weniger_1989]_).

    This function is based on ``get_CSEM1D_FD_QWE.m``, ``qwe.m``, and
    ``getBesselWeights.m`` from the source code distributed with [Key_2012]_.

    In the spline-version, ``hqwe`` checks how steep the decay of the
    wavenumber-domain result is, and calls QUAD for the very steep interval,
    for which QWE is not suited.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    Returns
    -------
    fEM : array
        Returns frequency-domain EM response.

    kcount : int
        Kernel count.

    conv : bool
        If true, QWE/QUAD converged. If not, <htarg> might have to be adjusted.

    """
    # Input params have an additional dimension for frequency, reduce here
    etaH = etaH[0, :]
    etaV = etaV[0, :]
    zetaH = zetaH[0, :]
    zetaV = zetaV[0, :]

    # Get rtol, atol, nquad, maxint, and pts_per_dec
    rtol, atol, nquad, maxint, pts_per_dec = qweargs[:5]

    # 1. PRE-COMPUTE THE BESSEL FUNCTIONS
    # at fixed quadrature points for each interval and multiply by the
    # corresponding Gauss quadrature weights

    # Get Gauss quadrature weights
    g_x, g_w = special.p_roots(nquad)

    # Compute n zeros of the Bessel function of the first kind of order 1 using
    # the Newton-Raphson method, which is fast enough for our purposes.  Could
    # be done with a loop for (but it is slower):
    # b_zero[i] = optimize.newton(special.j1, b_zero[i])

    # Initial guess using asymptotic zeros
    b_zero = np.pi*np.arange(1.25, maxint+1)

    # Newton-Raphson iterations
    for i in range(10):   # 10 is more than enough, usually stops in 5

        # Evaluate
        b_x0 = special.j1(b_zero)     # j0 and j1 have faster versions
        b_x1 = special.jv(2, b_zero)  # j2 does not have a faster version

        # The step length
        b_h = -b_x0/(b_x0/b_zero - b_x1)

        # Take the step
        b_zero += b_h

        # Check for convergence
        if all(np.abs(b_h) < 8*np.finfo(float).eps*b_zero):
            break

    # 2. COMPUTE THE QUADRATURE INTERVALS AND BESSEL FUNCTION WEIGHTS

    # Lower limit of integrand, a small but non-zero value
    xint = np.concatenate((np.array([1e-20]), b_zero))

    # Assemble the output arrays
    dx = np.repeat(np.diff(xint)/2, nquad)
    Bx = dx*(np.tile(g_x, maxint) + 1) + np.repeat(xint[:-1], nquad)
    BJ0 = special.j0(Bx)*np.tile(g_w, maxint)
    BJ1 = special.j1(Bx)*np.tile(g_w, maxint)

    # 3. START QWE

    # Intervals and lambdas for all offset
    intervals = xint/off[:, None]
    lambd = Bx/off[:, None]

    # Angle dependent factors
    factAng = kernel.angle_factor(angle, ab, msrc, mrec)

    # The following lines until
    #       "Call and return QWE, depending if spline or not"
    # are part of the splined routine. However, we calculate it here to get
    # the non-zero kernels, `k_used`.

    # New lambda, from min to max required lambda with pts_per_dec
    start = np.log10(lambd.min())
    stop = np.log10(lambd.max())

    # If not spline, we just calculate three lambdas to check
    if pts_per_dec == 0:
        ilambd = np.logspace(start, stop, 3)
    else:
        ilambd = np.logspace(start, stop, (stop-start)*pts_per_dec + 1)

    # Call the kernel
    PJ0, PJ1, PJ0b = kernel.wavenumber(zsrc, zrec, lsrc, lrec, depth,
                                       etaH[None, :], etaV[None, :],
                                       zetaH[None, :], zetaV[None, :],
                                       np.atleast_2d(ilambd), ab, xdirect,
                                       msrc, mrec, use_ne_eval)

    # Check which kernels have information
    k_used = list()
    for val in (PJ0, PJ1, PJ0b):
        k_used.append(np.any(val != 0))

    # Call and return QWE, depending if spline or not
    if pts_per_dec != 0:  # If spline, we calculate all kernels here

        # Interpolation : Has to be done separately on each PJ,
        # in order to work with multiple offsets which have different angles.
        if k_used[0]:
            sPJ0r = iuSpline(np.log10(ilambd), PJ0.real)
            sPJ0i = iuSpline(np.log10(ilambd), PJ0.imag)
        else:
            sPJ0r = None
            sPJ0i = None

        if k_used[1]:
            sPJ1r = iuSpline(np.log10(ilambd), PJ1.real)
            sPJ1i = iuSpline(np.log10(ilambd), PJ1.imag)
        else:
            sPJ1r = None
            sPJ1i = None

        if k_used[2]:
            sPJ0br = iuSpline(np.log10(ilambd), PJ0b.real)
            sPJ0bi = iuSpline(np.log10(ilambd), PJ0b.imag)
        else:
            sPJ0br = None
            sPJ0bi = None

        # Get quadargs: diff_quad, a, b, limit
        diff_quad, a, b, limit = qweargs[5:]

        # Set quadargs if not given:
        if not limit:
            limit = maxint
        if not a:
            a = intervals[:, 0]
        else:
            a = a*np.ones(off.shape)
        if not b:
            b = intervals[:, -1]
        else:
            b = b*np.ones(off.shape)

        # Check if we use QWE or SciPy's QUAD
        # If there are any steep decays within an interval we have to use QUAD,
        # as QWE is not designed for these intervals.
        check0 = np.log10(intervals[:, :-1])
        check1 = np.log10(intervals[:, 1:])
        numerator = np.zeros((off.size, maxint), dtype=complex)
        denominator = np.zeros((off.size, maxint), dtype=complex)

        if k_used[0]:
            numerator += sPJ0r(check0) + 1j*sPJ0i(check0)
            denominator += sPJ0r(check1) + 1j*sPJ0i(check1)

        if k_used[1]:
            numerator += sPJ1r(check0) + 1j*sPJ1i(check0)
            denominator += sPJ1r(check1) + 1j*sPJ1i(check1)

        if k_used[2]:
            numerator += sPJ0br(check0) + 1j*sPJ0bi(check0)
            denominator += sPJ0br(check1) + 1j*sPJ0bi(check1)

        doqwe = np.all((np.abs(numerator)/np.abs(denominator) < diff_quad), 1)

        # Pre-allocate output array
        fEM = np.zeros(off.size, dtype=complex)
        conv = True

        # Carry out SciPy's Quad if required
        if np.any(~doqwe):

            # Loop over offsets that require Quad
            for i in np.where(~doqwe)[0]:

                # Input-dictionary for quad
                iinp = {'a': a[i], 'b': b[i], 'epsabs': atol, 'epsrel': rtol,
                        'limit': limit}

                fEM[i], tc = quad(sPJ0r, sPJ0i, sPJ1r, sPJ1i, sPJ0br, sPJ0bi,
                                  ab, off[i], factAng[i], iinp)

                # Update conv
                conv *= tc

            # Return kcount=1 in case no QWE is calculated
            kcount = 1

        if np.any(doqwe):
            # Get EM-field at required offsets
            if k_used[0]:
                sPJ0 = sPJ0r(np.log10(lambd)) + 1j*sPJ0i(np.log10(lambd))
            if k_used[1]:
                sPJ1 = sPJ1r(np.log10(lambd)) + 1j*sPJ1i(np.log10(lambd))
            if k_used[2]:
                sPJ0b = sPJ0br(np.log10(lambd)) + 1j*sPJ0bi(np.log10(lambd))

            # Carry out and return the Hankel transform for this interval
            sEM = np.zeros_like(numerator, dtype=complex)
            if k_used[1]:
                sEM += np.sum(np.reshape(sPJ1*BJ1, (off.size, nquad, -1),
                              order='F'), 1)
                if ab in [11, 12, 21, 22, 14, 24, 15, 25]:  # Because of J2
                    # J2(kr) = 2/(kr)*J1(kr) - J0(kr)
                    sEM /= np.atleast_1d(off[:, np.newaxis])
            if k_used[2]:
                sEM += np.sum(np.reshape(sPJ0b*BJ0, (off.size, nquad, -1),
                                         order='F'), 1)
            if k_used[1] or k_used[2]:
                sEM *= factAng[:, np.newaxis]
            if k_used[0]:
                sEM += np.sum(np.reshape(sPJ0*BJ0, (off.size, nquad, -1),
                                         order='F'), 1)

            getkernel = sEM[doqwe, :]

            # Get QWE
            fEM[doqwe], kcount, tc = qwe(rtol, atol, maxint, getkernel,
                                         intervals[doqwe, :], None, None, None)
            conv *= tc

    else:  # If not spline, we define the wavenumber-kernel here
        def getkernel(i, inplambd, inpoff, inpfang):
            """Return wavenumber-domain-kernel as a fct of interval i."""

            # Indices and factor for this interval
            iB = i*nquad + np.arange(nquad)

            # PJ0 and PJ1 for this interval
            PJ0, PJ1, PJ0b = kernel.wavenumber(zsrc, zrec, lsrc, lrec, depth,
                                               etaH[None, :], etaV[None, :],
                                               zetaH[None, :], zetaV[None, :],
                                               np.atleast_2d(inplambd)[:, iB],
                                               ab, xdirect, msrc, mrec,
                                               use_ne_eval)

            # Carry out and return the Hankel transform for this interval
            gEM = np.zeros_like(inpoff, dtype=complex)
            if k_used[1]:
                gEM += inpfang*np.dot(PJ1[0, :], BJ1[iB])
                if ab in [11, 12, 21, 22, 14, 24, 15, 25]:  # Because of J2
                    # J2(kr) = 2/(kr)*J1(kr) - J0(kr)
                    gEM /= np.atleast_1d(inpoff)
            if k_used[2]:
                gEM += inpfang*np.dot(PJ0b[0, :], BJ0[iB])
            if k_used[0]:
                gEM += np.dot(PJ0[0, :], BJ0[iB])

            return gEM

        # Get QWE
        fEM, kcount, conv = qwe(rtol, atol, maxint, getkernel, intervals,
                                lambd, off, factAng)

    return fEM, kcount, conv


def hquad(zsrc, zrec, lsrc, lrec, off, angle, depth, ab, etaH, etaV, zetaH,
          zetaV, xdirect, quadargs, use_ne_eval, msrc, mrec):
    """Hankel Transform using the ``QUADPACK`` library.

    This routine uses the ``scipy.integrate.quad`` module, which in turn makes
    use of the Fortran library ``QUADPACK`` (``qagse``).

    It is massively (orders of magnitudes) slower than either ``fht`` or
    ``hqwe``, and is mainly here for completeness and comparison purposes. It
    always uses interpolation in the wavenumber domain, hence it generally will
    not be as precise as the other methods. However, it might work in some
    areas where the others fail.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    Returns
    -------
    fEM : array
        Returns frequency-domain EM response.

    kcount : int
        Kernel count. For HQUAD, this is 1.

    conv : bool
        If true, QUAD converged. If not, <htarg> might have to be adjusted.

    """

    # Get quadargs
    rtol, atol, limit, a, b, pts_per_dec = quadargs

    # Get required lambdas
    la = np.log10(a)
    lb = np.log10(b)
    ilambd = np.logspace(la, lb, (lb-la)*pts_per_dec + 1)

    # Call the kernel
    PJ0, PJ1, PJ0b = kernel.wavenumber(zsrc, zrec, lsrc, lrec, depth, etaH,
                                       etaV, zetaH, zetaV,
                                       np.atleast_2d(ilambd), ab, xdirect,
                                       msrc, mrec, use_ne_eval)

    # Interpolation in wavenumber domain: Has to be done separately on each PJ,
    # in order to work with multiple offsets which have different angles.
    # We check if the kernels are zero, to avoid unnecessary calculations.
    if np.any(PJ0 != 0):
        sPJ0r = iuSpline(np.log10(ilambd), PJ0.real)
        sPJ0i = iuSpline(np.log10(ilambd), PJ0.imag)
    else:
        sPJ0r = None
        sPJ0i = None

    if np.any(PJ1 != 0):
        sPJ1r = iuSpline(np.log10(ilambd), PJ1.real)
        sPJ1i = iuSpline(np.log10(ilambd), PJ1.imag)
    else:
        sPJ1r = None
        sPJ1i = None

    if np.any(PJ0b != 0):
        sPJ0br = iuSpline(np.log10(ilambd), PJ0b.real)
        sPJ0bi = iuSpline(np.log10(ilambd), PJ0b.imag)
    else:
        sPJ0br = None
        sPJ0bi = None

    # Get the angle factor
    factAng = kernel.angle_factor(angle, ab, msrc, mrec)

    # Pre-allocate output array
    fEM = np.zeros(off.size, dtype=complex)
    conv = True

    # Input-dictionary for quad
    iinp = {'a': a, 'b': b, 'epsabs': atol, 'epsrel': rtol, 'limit': limit}

    # Loop over offsets
    for i in range(off.size):
        fEM[i], tc = quad(sPJ0r, sPJ0i, sPJ1r, sPJ1i, sPJ0br, sPJ0bi, ab,
                          off[i], factAng[i], iinp)
        conv *= tc

    # Return the electromagnetic field
    # Second argument (1) is the kernel count, last argument is only for QWE.
    return fEM, 1, conv


# 2. Fourier transforms (frequency -> time)

def ffht(fEM, time, freq, ftarg):
    """Fourier Transform using the Digital Linear Filter method.


    It follows the Filter methodology [Anderson_1975]_, using Cosine- and
    Sine-filters; see ``fht`` for more information.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    This function is based on ``get_CSEM1D_TD_FHT.m`` from the source code
    distributed with [Key_2012]_.

    Returns
    -------
    tEM : array
        Returns time-domain EM response of ``fEM`` for given ``time``.

    conv : bool
        Only relevant for QWE/QUAD.

    """
    # Get ffhtargs
    ffhtfilt = ftarg[0]
    pts_per_dec = ftarg[1]
    kind = ftarg[2]  # Sine (`sin`) or cosine (`cos`)

    # Cast into Standard DLF format
    if pts_per_dec == 0:
        fEM = fEM.reshape(time.size, -1)

    # Carry out DLF
    tEM = dlf(fEM, 2*np.pi*freq, time, ffhtfilt, pts_per_dec, kind=kind)

    # Return the electromagnetic time domain field
    # (Second argument is only for QWE)
    return tEM, True


def fqwe(fEM, time, freq, qweargs):
    """Fourier Transform using Quadrature-With-Extrapolation.

    It follows the QWE methodology [Key_2012]_ for the Hankel transform, see
    ``hqwe`` for more information.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    This function is based on ``get_CSEM1D_TD_QWE.m`` from the source code
    distributed with [Key_2012]_.

    ``fqwe`` checks how steep the decay of the frequency-domain result is, and
    calls QUAD for the very steep interval, for which QWE is not suited.

    Returns
    -------
    tEM : array
        Returns time-domain EM response of ``fEM`` for given ``time``.

    conv : bool
        If true, QWE/QUAD converged. If not, <ftarg> might have to be adjusted.

    """
    # Get rtol, atol, nquad, maxint, diff_quad, a, b, and limit
    rtol, atol, nquad, maxint, _, diff_quad, a, b, limit, sincos = qweargs

    # Calculate quadrature intervals for all offset
    xint = np.concatenate((np.array([1e-20]), np.arange(1, maxint+1)*np.pi))
    if sincos == np.cos:  # Adjust zero-crossings if cosine-transform
        xint[1:] -= np.pi/2
    intervals = xint/time[:, None]

    # Get Gauss Quadrature Weights
    g_x, g_w = special.p_roots(nquad)

    # Pre-compute the Bessel functions at fixed quadrature points, multiplied
    # by the corresponding Gauss quadrature weight.
    dx = np.repeat(np.diff(xint)/2, nquad)
    Bx = dx*(np.tile(g_x, maxint) + 1) + np.repeat(xint[:-1], nquad)
    SS = sincos(Bx)*np.tile(g_w, maxint)

    # Interpolate in frequency domain
    tEM_rint = iuSpline(np.log10(2*np.pi*freq), fEM.real)
    tEM_iint = iuSpline(np.log10(2*np.pi*freq), -fEM.imag)

    # Check if we use QWE or SciPy's QUAD
    # If there are any steep decays within an interval we have to use QUAD, as
    # QWE is not designed for these intervals.
    check0 = np.log10(intervals[:, :-1])
    check1 = np.log10(intervals[:, 1:])
    doqwe = np.all((np.abs(tEM_rint(check0) + 1j*tEM_iint(check0)) /
                   np.abs(tEM_rint(check1) + 1j*tEM_iint(check1)) < diff_quad),
                   1)

    # Choose imaginary part if sine-transform, else real part
    if sincos == np.sin:
        tEM_int = tEM_iint
    else:
        tEM_int = tEM_rint

    # Set quadargs if not given:
    if not limit:
        limit = maxint
    if not a:
        a = intervals[:, 0]
    else:
        a = a*np.ones(time.shape)
    if not b:
        b = intervals[:, -1]
    else:
        b = b*np.ones(time.shape)

    # Pre-allocate output array
    tEM = np.zeros(time.size)
    conv = True

    # Carry out SciPy's Quad if required
    if np.any(~doqwe):
        def sEMquad(w, t):
            """Return scaled, interpolated value of tEM_int for ``w``."""
            return tEM_int(np.log10(w))*sincos(w*t)

        # Loop over times that require QUAD
        for i in np.where(~doqwe)[0]:
            out = integrate.quad(sEMquad, a[i], b[i], (time[i],), 1, atol,
                                 rtol, limit)
            tEM[i] = out[0]

            # If there is a fourth output from QUAD, it means it did not conv.
            if len(out) > 3:
                conv *= False

    # Carry out QWE for 'well-behaved' intervals
    if np.any(doqwe):
        sEM = tEM_int(np.log10(Bx/time[doqwe, None]))*SS
        tEM[doqwe], _, tc = qwe(rtol, atol, maxint, sEM, intervals[doqwe, :])
        conv *= tc

    return tEM, conv


def fftlog(fEM, time, freq, ftarg):
    """Fourier Transform using FFTLog.

    FFTLog is the logarithmic analogue to the Fast Fourier Transform FFT.
    FFTLog was presented in Appendix B of [Hamilton_2000]_ and published at
    <http://casa.colorado.edu/~ajsh/FFTLog>.

    This function uses a simplified version of ``pyfftlog``, which is a
    python-version of ``FFTLog``. For more details regarding ``pyfftlog`` see
    <https://github.com/prisae/pyfftlog>.

    Not the full flexibility of ``FFTLog`` is available here: Only the
    logarithmic FFT (``fftl`` in ``FFTLog``), not the Hankel transform (``fht``
    in ``FFTLog``). Furthermore, the following parameters are fixed:

       - ``kr`` = 1 (initial value)
       - ``kropt`` = 1 (silently adjusts ``kr``)
       - ``dir`` = 1 (forward)

    Furthermore, ``q`` is restricted to -1 <= q <= 1.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    Returns
    -------
    tEM : array
        Returns time-domain EM response of ``fEM`` for given ``time``.

    conv : bool
        Only relevant for QWE/QUAD.

    """
    # Get tcalc, dlnr, kr, rk, q; a and n
    _, _, q, mu, tcalc, dlnr, kr, rk = ftarg
    if mu > 0:  # Sine
        a = -fEM.imag
    else:       # Cosine
        a = fEM.real
    n = a.size

    # 1. Amplitude and Argument of kr^(-2 i y) U_mu(q + 2 i y)
    ln2kr = np.log(2.0/kr)
    d = np.pi/(n*dlnr)
    m = np.arange(1, (n+1)/2)
    y = m*d  # y = m*pi/(n*dlnr)

    if q == 0:  # unbiased case (q = 0)
        zp = special.loggamma((mu + 1)/2.0 + 1j*y)
        arg = 2.0*(ln2kr*y + zp.imag)

    else:       # biased case (q != 0)
        xp = (mu + 1.0 + q)/2.0
        xm = (mu + 1.0 - q)/2.0

        zp = special.loggamma(xp + 0j)
        zm = special.loggamma(xm + 0j)

        # Amplitude and Argument of U_mu(q)
        amp = np.exp(np.log(2.0)*q + zp.real - zm.real)
        # note +Im(zm) to get conjugate value below real axis
        arg = zp.imag + zm.imag

        # first element: cos(arg) = ±1, sin(arg) = 0
        argcos1 = amp*np.cos(arg)

        # remaining elements
        zp = special.loggamma(xp + 1j*y)
        zm = special.loggamma(xm + 1j*y)

        argamp = np.exp(np.log(2.0)*q + zp.real - zm.real)
        arg = 2*ln2kr*y + zp.imag + zm.imag

    argcos = np.cos(arg)
    argsin = np.sin(arg)

    # 2. Centre point of array
    jc = np.array((n + 1)/2.0)
    j = np.arange(n)+1

    # 3. a(r) = A(r) (r/rc)^[-dir*(q-.5)]
    a *= np.exp(-(q - 0.5)*(j - jc)*dlnr)

    # 4. transform a(r) -> ã(k)

    # 4.a normal FFT
    a = fftpack.rfft(a)

    # 4.b
    m = np.arange(1, n/2, dtype=int)  # index variable
    if q == 0:  # unbiased (q = 0) transform
        # multiply by (kr)^[- i 2 m pi/(n dlnr)] U_mu[i 2 m pi/(n dlnr)]
        ar = a[2*m-1]
        ai = a[2*m]
        a[2*m-1] = ar*argcos[:-1] - ai*argsin[:-1]
        a[2*m] = ar*argsin[:-1] + ai*argcos[:-1]
        # problematical last element, for even n
        if np.mod(n, 2) == 0:
            ar = argcos[-1]
            a[-1] *= ar

    else:  # biased (q != 0) transform
        # multiply by (kr)^[- i 2 m pi/(n dlnr)] U_mu[q + i 2 m pi/(n dlnr)]
        # phase
        ar = a[2*m-1]
        ai = a[2*m]
        a[2*m-1] = ar*argcos[:-1] - ai*argsin[:-1]
        a[2*m] = ar*argsin[:-1] + ai*argcos[:-1]

        a[0] *= argcos1
        a[2*m-1] *= argamp[:-1]
        a[2*m] *= argamp[:-1]

        # problematical last element, for even n
        if np.mod(n, 2) == 0:
            m = int(n/2)-3
            ar = argcos[m-1]*argamp[m-1]
            a[-1] *= ar

    # 4.c normal FFT back
    a = fftpack.irfft(a)

    # Ã(k) = ã(k) k^[-dir*(q+.5)] rc^[-dir*(q-.5)]
    #      = ã(k) (k/kc)^[-dir*(q+.5)] (kc rc)^(-dir*q) (rc/kc)^(dir*.5)
    a = a[::-1]*np.exp(-((q + 0.5)*(j - jc)*dlnr + q*np.log(kr) -
                       np.log(rk)/2.0))

    # Interpolate for the desired times
    ttEM = iuSpline(np.log10(tcalc), a)
    tEM = ttEM(np.log10(time))

    # (Second argument is only for QWE)
    return tEM, True


def fft(fEM, time, freq, ftarg):
    """Fourier Transform using the Fast Fourier Transform.

    The function is called from one of the modelling routines in :mod:`model`.
    Consult these modelling routines for a description of the input and output
    parameters.

    Returns
    -------
    tEM : array
        Returns time-domain EM response of ``fEM`` for given ``time``.

    conv : bool
        Only relevant for QWE/QUAD.

    """
    # Get ftarg values
    dfreq, nfreq, ntot, pts_per_dec = ftarg

    # If pts_per_dec, we have first to interpolate fEM to required freqs
    if pts_per_dec:
        sfEMr = iuSpline(np.log10(freq), fEM.real)
        sfEMi = iuSpline(np.log10(freq), fEM.imag)
        freq = np.arange(1, nfreq+1)*dfreq
        fEM = sfEMr(np.log10(freq)) + 1j*sfEMi(np.log10(freq))

    # Pad the frequency result
    fEM = np.pad(fEM, (0, ntot-nfreq), 'linear_ramp')

    # Carry out FFT
    ifftEM = fftpack.ifft(np.r_[fEM[1:], 0, fEM[::-1].conj()]).real
    stEM = 2*ntot*fftpack.fftshift(ifftEM*dfreq, 0)

    # Interpolate in time domain
    dt = 1/(2*ntot*dfreq)
    ifEM = iuSpline(np.linspace(-ntot, ntot-1, 2*ntot)*dt, stEM)
    tEM = ifEM(time)/2*np.pi  # (Multiplication of 2/pi in model.tem)

    # Return the electromagnetic time domain field
    # (Second argument is only for QWE)
    return tEM, True


# 3. Utilities

def dlf(signal, points, out_pts, filt, pts_per_dec, kind=None, factAng=None,
        ab=None):
    """Digital Linear Filter method.

    This is the kernel of the DLF method, used for the Hankel (``fht``) and the
    Fourier (``ffht``) Transforms. See ``fht`` for an extensive description.

    For the Hankel transform, `signal` contains 3 complex wavenumber-domain
    signals: (PJ0, PJ1, PJ0b), as returned from `kernel.wavenumber`. The Hankel
    DLF has two additional, optional parameters: `factAng`, as returned from
    `kernel.angle_factor`, and `ab`. The PJ0-kernel is the part of the
    wavenumber-domain calculation which contains a zeroth-order Bessel function
    and does NOT depend on the angle between source and receiver, only on
    offset. PJ0b and PJ1 are the parts of the wavenumber-domain calculation
    which contain a zeroth- and first-order Bessel function, respectively, and
    can depend on the angle between source and receiver. PJ0, PJ1, or PJ0b can
    also be None, if they are not used.

    For the Fourier transform, `signal` is a complex frequency-domain signal.
    The Fourier DLF requires one additional parameter, `kind`, which will be
    'cos' or 'sin'.

    """
    # 0. HANKEL/FOURIER-DEPENDING SETTINGS
    if isinstance(signal, tuple):
        # Hankel transform: 3 complex signals; respects `factAng` and `ab`
        hankel = True
        dtype = complex

        # Check if all angles are the same
        if factAng is None:
            factAng = np.array([1])
        one_angle = not np.any(factAng != factAng[0])
        angle_is_one = one_angle and factAng[0] == 1.0
        if one_angle:
            factAng = factAng[0]

        # Cast to list
        signal = list(signal)

    else:
        # Fourier transform: 1 complex signal; needs kind
        hankel = False
        dtype = float

        # Fourier independent of Angle
        one_angle = True

        # Real or -Imag part depending on kind (sine/cosine)
        if kind == 'sin':
            signal = -signal.imag
        else:
            signal = signal.real

        # Cast to list
        signal = [signal, ]

    # 1. PREPARE SIGNALS

    # Get kernels with information, to avoid unnecessary calculation
    k_used = list()
    for i, val in enumerate(signal):
        if val is None:
            k_used.append(False)
        else:
            inp_index = i  # Index of a kernel that is not None
            k_used.append(np.any(val != 0))

    # If all kernels are zero, return zero
    if sum(k_used) == 0:
        return np.zeros(signal[inp_index].shape[:-1], dtype=dtype)
    elif hankel:  # Get index of a non-zero kernel
        inp_index = np.arange(3)[np.array(k_used)][0]

    # Interpolation function
    def spline(values, points, new):
        """Return `values` at `points` interpolated in log10 at `new`."""
        out = iuSpline(np.log10(points), values.real)(np.log10(new))
        if hankel:
            out = out+1j*iuSpline(np.log10(points), values.imag)(np.log10(new))
        return out

    # Re-arranging and interpolation before DLF
    if pts_per_dec < 0:  # Lagged Convolution DLF: interp. in output domain
        # Lagged Convolution DLF: re-arrange signal

        # Get interpolation points in output domain
        _, int_pts = get_spline_values(filt, out_pts, pts_per_dec)

        # Re-arrange signal
        for i, val in enumerate(signal):
            if k_used[i]:  # Only if kernel contains info
                tmp_sig = np.concatenate((np.tile(val, int_pts.size).squeeze(),
                                         np.zeros(int_pts.size)))
                signal[i] = tmp_sig.reshape(int_pts.size, -1)[:,
                                                              :filt.base.size]

    elif pts_per_dec > 0:  # Splined DLF: interpolate in input domain
        # Splined DLF: interpolate signal here
        new = filt.base/out_pts[:, None]
        for i, val in enumerate(signal):
            if k_used[i]:  # Only if kernel contains info
                signal[i] = spline(val, points, new)

    # 2. APPLY DLF
    if hankel:  # Hankel transform
        inp_PJ0, inp_PJ1, inp_PJ0b = signal

        # If Kernel is all zeroes we just put zeroes instead of carrying out
        # the DLF
        alt_pre = np.zeros(signal[inp_index].shape[:-1], dtype=complex)

        if pts_per_dec != 0 and not one_angle:
            # Varying angle with either lagged or splined DLF.
            # If not all offsets are in one line from the source, hence do not
            # have the same angle, the DLF has to be done separately for
            # angle-dependent and angle-independent parts.
            out_angle = alt_pre.copy()
            out_noang = alt_pre.copy()

            # Do transform for the used kernels
            if k_used[0]:  # J0
                out_noang += np.dot(inp_PJ0, filt.j0)

            if k_used[1]:  # J1
                out_angle += np.dot(inp_PJ1, filt.j1)
                if ab in [11, 12, 21, 22, 14, 24, 15, 25]:  # Because of J2
                    # J2(kr) = 2/(kr)*J1(kr) - J0(kr)
                    if pts_per_dec < 0:  # Lagged Convolution
                        out_angle /= int_pts
                    else:  # Splined
                        out_angle /= out_pts

            if k_used[2]:  # J0b
                out_angle += np.dot(inp_PJ0b, filt.j0)

            if pts_per_dec > 0:
                # If splined we can add them here, as the interpolation
                # is already done.
                out_signal = factAng*out_angle + out_noang

        else:
            # With the standard DLF (one_angle or not), and with the splined
            # DLF but one_angle, we can combine PJ0 and PJ0b to save one DLF.
            out_signal = alt_pre.copy()

            # Do transform for the used kernels
            if k_used[1]:  # J1
                out_signal += np.dot(inp_PJ1, filt.j1)
                if ab in [11, 12, 21, 22, 14, 24, 15, 25]:  # Because of J2
                    # J2(kr) = 2/(kr)*J1(kr) - J0(kr)
                    if pts_per_dec < 0:  # Lagged Convolution
                        out_signal /= int_pts
                    else:  # Splined
                        out_signal /= out_pts

            if k_used[2]:  # J0b
                out_signal += np.dot(inp_PJ0b, filt.j0)

            # Angle factor
            if not angle_is_one:
                out_signal *= factAng

            if k_used[0]:  # J0
                out_signal += np.dot(inp_PJ0, filt.j0)

    else:  # Fourier transform
        out_signal = np.dot(signal[0], getattr(filt, kind))

    # 3. IF LAGGED CONVOLUTION, INTERPOLATE NOW TO OUTPUT DOMAIN POINTS
    if pts_per_dec < 0:

        if not one_angle:  # Separately on out_noang and out_angle

            if k_used[0]:  # Only if kernel contains info
                int_noang = spline(out_noang[::-1], int_pts[::-1], out_pts)
            else:
                int_noang = np.zeros(out_pts.shape, dtype=complex)

            # J1 or J2 are always used except for ab=33; however ab=33 is
            # angle-independent, so we don't have to check here.
            int_angle = spline(out_angle[::-1], int_pts[::-1], out_pts)

            # Angle dependency
            out_signal = factAng*int_angle + int_noang

        else:  # If only one angle or Fourier
            out_signal = spline(out_signal[::-1], int_pts[::-1], out_pts)

    # Return the signal in the output domain
    return out_signal/out_pts


def qwe(rtol, atol, maxint, inp, intervals, lambd=None, off=None,
        factAng=None):
    """Quadrature-With-Extrapolation.

    This is the kernel of the QWE method, used for the Hankel (``hqwe``) and
    the Fourier (``fqwe``) Transforms. See ``hqwe`` for an extensive
    description.

    This function is based on ``qwe.m`` from the source code distributed with
    [Key_2012]_.

    """
    def getweights(i, inpint):
        """Return weights for this interval."""
        return (np.atleast_2d(inpint)[:,  i+1] - np.atleast_2d(inpint)[:, i])/2

    # 1. Calculate the first interval for all offsets
    if hasattr(inp, '__call__'):  # Hankel and not spline
        EM0 = inp(0, lambd, off, factAng)
    else:                         # Fourier or Hankel with spline
        EM0 = inp[:, 0]
    EM0 *= getweights(0, intervals)

    # 2. Pre-allocate arrays and initialize
    EM = np.zeros(EM0.size, dtype=EM0.dtype)                # EM array
    om = np.ones(EM0.size, dtype=bool)                      # Convergence array
    S = np.zeros((EM0.size, maxint), dtype=EM0.dtype)  # Working arr. 4 recurs.
    relErr = np.zeros((EM0.size, maxint))                   # Relative error
    extrap = np.zeros((EM0.size, maxint), dtype=EM0.dtype)  # extrap. result
    kcount = 1  # Initialize kernel count (only important for Hankel)

    # 3. The extrapolation transformation loop
    for i in range(1, maxint):
        # 3.a Calculate the field for this interval
        if hasattr(inp, '__call__'):  # Hankel and not spline
            EMi = inp(i, lambd[om, :], off[om], factAng[om])
            kcount += 1  # Update count
        else:                         # Fourier or Hankel with spline
            EMi = inp[om, i]
        EMi *= getweights(i, intervals[om, :])

        # 3.b Compute Shanks transformation
        # Using the epsilon algorithm: structured after [Weniger_1989]_, p26.
        S[:, i][om] = S[:, i-1][om] + EMi  # working array for transformation

        # Recursive loop
        aux2 = np.zeros(om.sum(), dtype=EM0.dtype)
        for k in range(i, 0, -1):
            aux1, aux2 = aux2, S[om, k-1]
            ddff = S[om, k] - aux2
            S[om, k-1] = np.where(np.abs(ddff) < np.finfo(np.double).tiny,
                                  np.finfo(np.double).max, aux1 + 1/ddff)

        # The extrapolated result plus the first interval term
        extrap[om, i-1] = S[om, np.mod(i, 2)] + EM0[om]

        # 3.c Analyze for convergence
        if i > 1:
            # Calculate relative and absolute error
            rErr = (extrap[om, i-1] - extrap[om, i-2])/extrap[om, i-1]
            relErr[om, i-1] = np.abs(rErr)
            abserr = atol/np.abs(extrap[om, i-1])

            # Update booleans
            om[om] *= relErr[om, i-1] >= rtol + abserr

            # Store in EM
            EM[om] = extrap[om, i-1]

        if (~om).all():
            break

    # 4. Cleaning up

    # Warning if maxint is potentially too small
    conv = i+1 != maxint

    # Catch the ones that did not converge
    EM[om] = extrap[om, i-1]

    # Set np.finfo(np.double).max to 0
    EM.real[EM.real == np.finfo(np.double).max] = 0

    return EM, kcount, conv


def quad(sPJ0r, sPJ0i, sPJ1r, sPJ1i, sPJ0br, sPJ0bi, ab, off, factAng, iinp):
    """Quadrature for Hankel transform.

    This is the kernel of the QUAD method, used for the Hankel transforms
    ``hquad`` and ``hqwe`` (where the integral is not suited for QWE).

    """

    # Define the quadrature kernels
    def quad_PJ0(klambd, sPJ0, koff):
        """Quadrature for PJ0."""
        return sPJ0(np.log10(klambd))*special.j0(koff*klambd)

    def quad_PJ1(klambd, sPJ1, ab, koff, kang):
        """Quadrature for PJ1."""

        tP1 = kang*sPJ1(np.log10(klambd))
        if ab in [11, 12, 21, 22, 14, 24, 15, 25]:  # Because of J2
            # J2(kr) = 2/(kr)*J1(kr) - J0(kr)
            tP1 /= koff

        return tP1*special.j1(koff*klambd)

    def quad_PJ0b(klambd, sPJ0b, koff, kang):
        """Quadrature for PJ0b."""
        return kang*sPJ0b(np.log10(klambd))*special.j0(koff*klambd)

    # Pre-allocate output
    conv = True
    out = np.array(0.0+0.0j)

    # Carry out quadrature for required kernels
    iinp['full_output'] = 1

    if sPJ0r is not None:
        re = integrate.quad(quad_PJ0, args=(sPJ0r, off), **iinp)
        im = integrate.quad(quad_PJ0, args=(sPJ0i, off), **iinp)
        out += re[0] + 1j*im[0]
        # If there is a fourth output from QUAD, it means it did not converge
        if (len(re) or len(im)) > 3:
            conv = False

    if sPJ1r is not None:
        re = integrate.quad(quad_PJ1, args=(sPJ1r, ab, off, factAng), **iinp)
        im = integrate.quad(quad_PJ1, args=(sPJ1i, ab, off, factAng), **iinp)
        out += re[0] + 1j*im[0]
        # If there is a fourth output from QUAD, it means it did not converge
        if (len(re) or len(im)) > 3:
            conv = False

    if sPJ0br is not None:
        re = integrate.quad(quad_PJ0b, args=(sPJ0br, off, factAng), **iinp)
        im = integrate.quad(quad_PJ0b, args=(sPJ0bi, off, factAng), **iinp)
        out += re[0] + 1j*im[0]
        # If there is a fourth output from QUAD, it means it did not converge
        if (len(re) or len(im)) > 3:
            conv = False

    # Collect the results
    return out, conv


def get_spline_values(filt, inp, nr_per_dec=None):
    """Return required calculation points."""

    # Standard DLF
    if nr_per_dec == 0:
        return filt.base/inp[:, None], inp

    # Get min and max required out-values (depends on filter and inp-value)
    outmax = filt.base[-1]/inp.min()
    outmin = filt.base[0]/inp.max()

    # Define number of out-values, depending on pts_per_dec
    def nr_of_out(outmax, outmin, pts_per_dec):
        """Number of out-values."""
        return int(np.ceil(np.log(outmax/outmin)*pts_per_dec) + 1)

    # Get pts_per_dec
    if nr_per_dec < 0:  # Lagged Convolution DLF
        pts_per_dec = 1/np.log(filt.factor)

    else:  # Splined DLF
        pts_per_dec = nr_per_dec

    # Calculate number of output values
    nout = nr_of_out(outmax, outmin, pts_per_dec)

    # Min-nout check, becaus the cubic InterpolatedUnivariateSpline needs at
    # least 4 points.
    if nr_per_dec < 0:  # Lagged Convolution DLF
        # Lagged Convolution DLF interpolates in output domain, so `new_inp`
        # needs to have at least 4 points.
        if nout-filt.base.size < 3:
            nout = filt.base.size+3

    else:  # Splined DLF
        # Splined DLF interpolates in input domain, so `out` needs to have at
        # least 4 points. This should always be the case, we're just overly
        # cautious here.
        if nout < 4:
            nout = 4

    # Calculate output values
    out = np.exp(np.arange(np.log(outmin), np.log(outmin) + nout/pts_per_dec,
                           1/pts_per_dec))

    # Only necessary if standard spline is used. We need to calculate the new
    # input values, as spline is carried out in the input domain. Else spline
    # is carried out in output domain and the new input values are not used.
    new_inp = inp.max()*np.exp(-np.arange(nout - filt.base.size + 1) /
                               pts_per_dec)

    # Return output values
    return np.atleast_2d(out), new_inp


def fhti(rmin, rmax, n, q, mu):
    """Return parameters required for FFTLog."""

    # Central point log10(r_c) of periodic interval
    logrc = (rmin + rmax)/2

    # Central index (1/2 integral if n is even)
    nc = (n + 1)/2.

    # Log spacing of points
    dlogr = (rmax - rmin)/n
    dlnr = dlogr*np.log(10.)

    # Get low-ringing kr
    y = 1j*np.pi/(2.0*dlnr)
    zp = special.loggamma((mu + 1.0 + q)/2.0 + y)
    zm = special.loggamma((mu + 1.0 - q)/2.0 + y)
    arg = np.log(2.0)/dlnr + (zp.imag + zm.imag)/np.pi
    kr = np.exp((arg - np.round(arg))*dlnr)

    # Calculate required input x-values (freq); angular freq -> freq
    freq = 10**(logrc + (np.arange(1, n+1) - nc)*dlogr)/(2*np.pi)

    # Calculate tcalc with adjusted kr
    logkc = np.log10(kr) - logrc
    tcalc = 10**(logkc + (np.arange(1, n+1) - nc)*dlogr)

    # rk = r_c/k_r; adjust for Fourier transform scaling
    rk = 10**(logrc - logkc)*np.pi/2

    return freq, tcalc, dlnr, kr, rk
