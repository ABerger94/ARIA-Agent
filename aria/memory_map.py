"""Memory constellation layout (Alek, 2026-10-27).

Pure numpy, no Qt: turn memory rows (dicts with 'category',
'key' and an optional embedding 'vec') into points on a unit
sphere plus faint edges between near-identical facts — the
star ball the dashboard's Memory tab and centerpiece draw.

Facts WITH embeddings are placed by meaning: PCA (first three
components) of the centred embedding matrix, each point
normalised onto the sphere, so facts that mean similar things
land near each other and topic clusters form on their own.
Facts WITHOUT an embedding (saved before embeddings, or while
the embedder was down) are parked on a ring: one tilted ring
per category, points spread evenly around it — visibly a
different population from the meaning-placed stars, which is
honest: she knows the fact, she just never embedded it.

Deterministic throughout (hash-seeded angles, no RNG): the
same store always draws the same ball.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

import numpy as np

EDGE_COS = 0.72            # min cosine similarity for a drawn edge
EDGE_MAX_PER_POINT = 2     # each star links to its closest few


def _hash01(text: str) -> float:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def layout_points(rows: List[Dict]) -> Tuple[np.ndarray, List[Tuple[int, int]]]:
    """rows -> (points (n,3) on the unit sphere, edges [(i, j)]).

    Row order is preserved: points[i] is rows[i]'s star."""
    n = len(rows)
    pts = np.zeros((n, 3), dtype=np.float64)
    if n == 0:
        return pts, []

    have = [i for i, r in enumerate(rows)
            if r.get("vec") is not None and len(r["vec"])]
    placed = set()
    if len(have) >= 2:
        X = np.stack([np.asarray(rows[i]["vec"], dtype=np.float64)
                      for i in have])
        Xc = X - X.mean(axis=0)
        try:
            _u, _s, vt = np.linalg.svd(Xc, full_matrices=False)
            P = Xc @ vt[:3].T
            if P.shape[1] < 3:
                P = np.pad(P, ((0, 0), (0, 3 - P.shape[1])))
        except Exception:
            P = np.zeros((len(have), 3))
            P[:, : min(3, Xc.shape[1])] = Xc[:, :3]
        for j, i in enumerate(have):
            p = _unit(P[j])
            if float(np.linalg.norm(p)) < 1e-9:
                # A fact identical to the mean has no direction;
                # give it a deterministic one from its key.
                a = _hash01(str(rows[i].get("key", ""))) * 2 * np.pi
                p = np.array([np.cos(a), np.sin(a), 0.3])
                p = _unit(p)
            pts[i] = p
            placed.add(i)

    # Ring placement for everything unplaced (no embedding, or a
    # lone embedded fact with nothing to triangulate against).
    ring = [i for i in range(n) if i not in placed]
    by_cat: Dict[str, List[int]] = {}
    for i in ring:
        by_cat.setdefault(str(rows[i].get("category", "")), []).append(i)
    for cat, idxs in by_cat.items():
        tilt = np.radians(_hash01("tilt:" + cat) * 56 - 28)
        offset = _hash01("ring:" + cat) * 2 * np.pi
        ct, st = np.cos(tilt), np.sin(tilt)
        for k, i in enumerate(idxs):
            ang = offset + 2 * np.pi * (k + 0.5) / max(len(idxs), 1)
            x, y = np.cos(ang), np.sin(ang)
            pts[i] = (x, y * ct, y * st)   # equator ring, tilted

    # Edges: cosine similarity on the ORIGINAL vectors (never
    # the projected ones), each star to its closest few.
    edges = set()
    if len(have) >= 2:
        V = {i: _unit(np.asarray(rows[i]["vec"], dtype=np.float64))
             for i in have}
        for i in have:
            sims = sorted(
                ((float(V[i] @ V[j]), j) for j in have if j != i),
                reverse=True)
            for sim, j in sims[:EDGE_MAX_PER_POINT]:
                if sim >= EDGE_COS:
                    edges.add((min(i, j), max(i, j)))
    return pts, sorted(edges)
