#!/usr/bin/env python3
"""
DP utility simulation for ReportItNow Transparency Dashboard.

Assumes the dashboard induces a 20x increase in reporting (the legitimacy-signal
argument requires that visibility produces a reporting lift; we adopt the strongest
plausible value as a stress test).

Baselines (FY25, India):
- Long-tail company (~100-1,000 emp): ~0-1 cases/yr
- Mid (1,000-10,000 emp):              ~1-5 cases/yr
- Large (10,000-100,000 emp):          ~10-60 cases/yr
- Mega (>=100,000 emp):                ~80-200 cases/yr (TCS=110, Infosys=98, Wipro=182)

Projected (20x):
- Long-tail:  0-20
- Mid:        20-100
- Large:      200-1,200
- Mega:       1,600-4,000

Mechanism (annual release, eps_c = eps_r = eps_tau = 0.5):
- tilde_n:    n + Laplace(1/eps_c), sensitivity 1
- tilde_rho:  noised binary fraction; we use the per-record randomized-response style:
              add Laplace(1/eps_r) to the count of resolved cases, then divide by tilde_n
              (with clipping to [0,1]). Sensitivity 1 on numerator.
- Small-n suppression: if n < n_min, no numeric release.
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# ---------- Parameters ----------
EPS_C = 0.5
EPS_R = 0.5
EPS_TAU = 0.5
N_MIN = 5                    # below this, suppress numeric release
N_TRIALS = 20000             # Monte Carlo samples per point
RHO_TRUE = 0.70              # fixed true resolution rate
TAU_TRUE = 65                # fixed true mean resolution time (days)
TAU_CLAMP = 150              # clamp range L (paper: Sections 11(4)+13(4) admissible max)

# True caseloads to evaluate (post-20x lift)
N_GRID = np.array([0, 1, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 4000])

rng = np.random.default_rng(42)

def noised_count(n, n_trials=N_TRIALS):
    """tilde_n = n + Laplace(1/eps_c), clamped to >= 0."""
    noise = rng.laplace(loc=0.0, scale=1.0/EPS_C, size=n_trials)
    return np.maximum(0, n + noise)

def noised_resolution_rate(n, rho_true=RHO_TRUE, n_trials=N_TRIALS):
    """
    Per paper Sec. V Mechanism: tilde_rho = clamp(rho + Lap((2/n)/eps_r), [0,1]).
    Sensitivity Delta_rho <= 2/n under unbounded neighbouring.
    """
    if n == 0:
        return np.zeros(n_trials)
    scale = (2.0 / n) / EPS_R
    return np.clip(rho_true + rng.laplace(loc=0.0, scale=scale, size=n_trials), 0.0, 1.0)

def noised_mean_time(n, tau_true=TAU_TRUE, n_trials=N_TRIALS):
    """tilde_tau = tau + Laplace(L / (n eps_tau)), where L is the clamp range."""
    if n == 0:
        return np.full(n_trials, np.nan)
    scale = TAU_CLAMP / (n * EPS_TAU)
    return np.clip(tau_true + rng.laplace(loc=0.0, scale=scale, size=n_trials), 0, TAU_CLAMP)

# Compute envelopes
count_lo, count_mid, count_hi = [], [], []
rho_lo, rho_mid, rho_hi = [], [], []
tau_lo, tau_mid, tau_hi = [], [], []

for n in N_GRID:
    nc = noised_count(n)
    count_lo.append(np.percentile(nc, 5))
    count_mid.append(np.percentile(nc, 50))
    count_hi.append(np.percentile(nc, 95))

    nr = noised_resolution_rate(n)
    rho_lo.append(np.percentile(nr, 5))
    rho_mid.append(np.percentile(nr, 50))
    rho_hi.append(np.percentile(nr, 95))

    nt = noised_mean_time(n)
    tau_lo.append(np.percentile(nt, 5))
    tau_mid.append(np.percentile(nt, 50))
    tau_hi.append(np.percentile(nt, 95))

count_lo = np.array(count_lo); count_mid = np.array(count_mid); count_hi = np.array(count_hi)
rho_lo = np.array(rho_lo); rho_mid = np.array(rho_mid); rho_hi = np.array(rho_hi)

# Suppress points below N_MIN in display
def suppression_mask(grid):
    return grid >= N_MIN

mask = suppression_mask(N_GRID)

# ---------- Figure ----------
plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 9,
    'axes.linewidth': 0.5,
    'xtick.major.width': 0.5,
    'ytick.major.width': 0.5,
    'lines.linewidth': 1.2,
})

fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.9), constrained_layout=True)

# --- Panel A: noised count ---
ax = axes[0]
ax.plot([1e-1, 1e4], [1e-1, 1e4], color='gray', linestyle=':', linewidth=0.8, label='exact $n$')

# 90% CI band
ax.fill_between(N_GRID[mask], count_lo[mask], count_hi[mask],
                color='#3a6fb0', alpha=0.25, label=r'90% CI of $\tilde n$')
ax.plot(N_GRID[mask], count_mid[mask], color='#1f3d6e', marker='o', markersize=3.5,
        label=r'median $\tilde n$')

# Suppressed region annotation
ax.axvspan(0.5, N_MIN, color='#cccccc', alpha=0.35)
ax.text(2.2, 1700, f'$n < n_{{\\min}}={N_MIN}$\nsuppressed', ha='center', va='top',
        fontsize=7.5, color='#555555')

ax.set_xscale('log')
ax.set_yscale('symlog', linthresh=1)
ax.set_xlim(0.7, 5000)
ax.set_ylim(0, 5000)
ax.set_xlabel('true case count $n$ (per fiscal year)')
ax.set_ylabel(r'noised count $\tilde n$')
ax.set_title(r'(a) Count release ($\varepsilon_c = 0.5$)', fontsize=9.5)
ax.grid(alpha=0.25, linewidth=0.4)
ax.legend(loc='lower right', fontsize=7.5, frameon=True, framealpha=0.9)

# --- Panel B: noised resolution rate ---
ax = axes[1]
ax.axhline(RHO_TRUE, color='gray', linestyle=':', linewidth=0.8, label=r'true $\rho=0.70$')
ax.fill_between(N_GRID[mask], rho_lo[mask], rho_hi[mask],
                color='#b06a3a', alpha=0.25, label=r'90% CI of $\tilde\rho$')
ax.plot(N_GRID[mask], rho_mid[mask], color='#6e3d1f', marker='o', markersize=3.5,
        label=r'median $\tilde\rho$')

ax.axvspan(0.5, N_MIN, color='#cccccc', alpha=0.35)

ax.set_xscale('log')
ax.set_xlim(0.7, 5000)
ax.set_ylim(0, 1.05)
ax.set_xlabel('true case count $n$ (per fiscal year)')
ax.set_ylabel(r'noised resolution rate $\tilde\rho$')
ax.set_title(r'(b) Resolution rate release ($\varepsilon_r = 0.5$)', fontsize=9.5)
ax.grid(alpha=0.25, linewidth=0.4)
ax.legend(loc='lower right', fontsize=7.5, frameon=True, framealpha=0.9)

plt.savefig('dp_utility.pdf', bbox_inches='tight', pad_inches=0.02)
plt.savefig('dp_utility.png', bbox_inches='tight', pad_inches=0.02, dpi=180)
print("Saved dp_utility.pdf and dp_utility.png")

# ---------- Numbers table ----------
print("\n=== DP utility at four deployment bands (post-20x reporting lift) ===\n")
print(f"{'Band':<22} {'n (yr)':>10} {'tilde_n 90% CI':>20} {'tilde_rho 90% CI':>22} {'tilde_tau 90% CI':>22}")
print("-" * 100)

bands = [
    ('Long-tail (100-1k emp)',  10),
    ('Mid (1k-10k emp)',        60),
    ('Large (10k-100k emp)',    600),
    ('Mega (>=100k emp)',       3000),
]
for label, n in bands:
    nc = noised_count(n, n_trials=50000)
    nr = noised_resolution_rate(n, n_trials=50000)
    nt = noised_mean_time(n, n_trials=50000)
    nc_lo, nc_hi = np.percentile(nc, [5, 95])
    nr_lo, nr_hi = np.percentile(nr, [5, 95])
    nt_lo, nt_hi = np.percentile(nt, [5, 95])
    print(f"{label:<22} {n:>10} {f'[{nc_lo:.1f}, {nc_hi:.1f}]':>20} "
          f"{f'[{nr_lo:.3f}, {nr_hi:.3f}]':>22} {f'[{nt_lo:.1f}, {nt_hi:.1f}] d':>22}")
