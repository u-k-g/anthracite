# SPDX-License-Identifier: LGPL-2.1-or-later
"""Reference-based geometry scoring. All poses are proper rigid transforms.

The document remains authoritative. This module only operates on detached shapes.
Sampling and registration are deterministic; finite search is not a proof of the
globally optimal pose. Automatic interface regions describe geometry, not intent.
"""
import itertools
import math

import FreeCAD as App
import Part


def _libraries():
    try:
        import numpy as np
        from scipy.spatial import cKDTree
        from scipy.spatial.transform import Rotation
        from scipy.stats import qmc
        from scipy.optimize import linear_sum_assignment
    except ImportError as error:
        raise ValueError("Geometry comparison requires NumPy and SciPy in FreeCAD's Python environment.") from error
    return np, cKDTree, Rotation, qmc, linear_sum_assignment


def _vector(v):
    return [v.x, v.y, v.z]


def _center(shape, np):
    solids = shape.Solids
    mass = sum(s.Volume for s in solids)
    if mass <= 0:
        raise ValueError("Comparison needs positive-volume solids.")
    return sum((s.Volume * np.array(_vector(s.CenterOfMass)) for s in solids), np.zeros(3)) / mass


def _inertia_frame(shape, np):
    center = _center(shape, np)
    inertia = np.zeros((3, 3))
    for solid in shape.Solids:
        matrix = solid.MatrixOfInertia
        offset = np.array(_vector(solid.CenterOfMass)) - center
        inertia += np.array([[getattr(matrix, f"A{i+1}{j+1}") for j in range(3)] for i in range(3)])
        inertia += solid.Volume * (np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset))
    _, frame = np.linalg.eigh(inertia)
    if np.linalg.det(frame) < 0:
        frame[:, 0] *= -1
    return frame


def _octahedral(np):
    result = []
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            matrix = np.eye(3)[:, permutation] * signs
            if np.linalg.det(matrix) > 0:
                result.append(matrix)
    return result


def _mesh(shape, deflection, np):
    vertices, triangles = shape.tessellate(deflection)
    if not triangles:
        raise ValueError("Shape cannot be tessellated for comparison.")
    vertices = np.array([_vector(v) for v in vertices])
    triangles = np.asarray(triangles, dtype=int)
    return vertices, triangles


def _samples(shape, count, deflection, np, qmc):
    vertices, triangles = _mesh(shape, deflection, np)
    corners = vertices[triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = np.linalg.norm(normals, axis=1)
    keep = areas > 1e-16
    corners, normals, areas = corners[keep], normals[keep], areas[keep]
    if not len(areas):
        raise ValueError("Shape has no positive-area tessellation.")
    normals /= areas[:, None]
    # Independent deterministic area-weighted samples, not triangle vertices.
    draws = qmc.Sobol(3, scramble=False).random_base2(int(math.ceil(math.log2(count + 1))))[1:count + 1]
    indices = np.searchsorted(np.cumsum(areas) / areas.sum(), draws[:, 0], side="right")
    indices = np.minimum(indices, len(areas) - 1)
    root = np.sqrt(draws[:, 1])
    weights = np.column_stack((1-root, root*(1-draws[:, 2]), root*draws[:, 2]))
    points = (corners[indices] * weights[:, :, None]).sum(axis=1)
    return points, normals[indices]


def _transform(shape, rotation, translation):
    matrix = App.Matrix()
    for i in range(3):
        for j in range(3):
            setattr(matrix, f"A{i+1}{j+1}", float(rotation[i, j]))
        setattr(matrix, f"A{i+1}4", float(translation[i]))
    # A detached BRep, with no scaling or mutation of the input's placement.
    result = shape.copy()
    result.transformShape(matrix)
    return result


def _fit(source, target, np, weights=None):
    weights = np.ones(len(source)) if weights is None else weights
    weights = weights / weights.sum()
    a, b = weights @ source, weights @ target
    u, _, vt = np.linalg.svd((source-a).T @ ((target-b) * weights[:, None]))
    correction = np.eye(3)
    correction[2, 2] = np.linalg.det(vt.T @ u.T)
    rotation = vt.T @ correction @ u.T
    return rotation, b-rotation @ a


def _icp(source, target, rotation, translation, np, KDTree, Rotation, span):
    tree = KDTree(target)
    previous = float("inf")
    for _ in range(28):
        moved = source @ rotation.T + translation
        distances, indices = tree.query(moved)
        # Trim the worst correspondences; never rescale to fit a different part.
        keep = distances <= max(np.quantile(distances, .85), span * 1e-7)
        if keep.sum() < 6:
            break
        step_r, step_t = _fit(moved[keep], target[indices[keep]], np)
        rotation, translation = step_r @ rotation, step_r @ translation + step_t
        error = float(np.mean(distances[keep] ** 2))
        if abs(previous-error) < span**2 * 1e-12:
            break
        previous = error
    return rotation, translation


def _fpfh(points, normals, radius, np, KDTree):
    """33-bin angular SPFH plus inverse-distance neighbour aggregation."""
    tree = KDTree(points)
    distances, neighbours = tree.query(points, k=min(32, len(points)))
    features = np.zeros((len(points), 33))
    for i in range(len(points)):
        keep = (distances[i] > 1e-10) & (distances[i] <= radius)
        js, ds = neighbours[i][keep], distances[i][keep]
        if not len(js):
            continue
        direction = (points[js] - points[i]) / ds[:, None]
        u = np.broadcast_to(normals[i], direction.shape).copy()
        other = normals[js].copy()
        # PFH's pair ordering makes descriptors independent of query direction.
        swap = np.abs((u*direction).sum(axis=1)) < np.abs((other*direction).sum(axis=1))
        u[swap], other[swap] = other[swap].copy(), u[swap].copy()
        direction[swap] *= -1
        v = np.cross(direction, u)
        lengths = np.linalg.norm(v, axis=1)
        valid = lengths > 1e-10
        v = v[valid] / lengths[valid, None]
        u, other, direction = u[valid], other[valid], direction[valid]
        w = np.cross(u, v)
        values = [(v*other).sum(axis=1), (u*direction).sum(axis=1),
                  np.arctan2((w*other).sum(axis=1), (u*other).sum(axis=1))]
        for k, values_k in enumerate(values):
            limit = math.pi if k == 2 else 1
            histogram = np.histogram(values_k, bins=11, range=(-limit, limit))[0]
            features[i, k*11:(k+1)*11] = histogram / max(1, histogram.sum())
    result = features.copy()
    for i in range(len(points)):
        keep = (distances[i] > 1e-10) & (distances[i] <= radius)
        weights = 1 / distances[i][keep]
        if len(weights):
            result[i] += weights @ features[neighbours[i][keep]] / weights.sum()
    return result


def _feature_pose(source, source_n, target, target_n, span, np, KDTree):
    a = _fpfh(source, source_n, span*.25, np, KDTree)
    b = _fpfh(target, target_n, span*.25, np, KDTree)
    _, js = KDTree(b).query(a)
    _, reverse = KDTree(a).query(b)
    ids = np.arange(len(source))
    keep = (reverse[js] == ids) & (np.linalg.norm(a, axis=1) > 0)
    if keep.sum() < 6:
        return None
    x, y = source[keep], target[js[keep]]
    # FGR-style graduated non-convex line process, solved by weighted rigid SVD.
    rotation, translation = _fit(x, y, np)
    mu = span**2
    for _ in range(48):
        squared = ((x @ rotation.T + translation-y)**2).sum(axis=1)
        weights = (mu / (mu+squared))**2
        rotation, translation = _fit(x, y, np, weights)
        mu = max(mu / 1.4, (span*.002)**2)
    return rotation, translation


def _axis_frames(shape, np):
    axes = []
    for face in shape.Faces:
        surface = face.Surface
        if type(surface).__name__ in ("Plane", "Cylinder", "Cone", "Torus"):
            axis = np.array(_vector(surface.Axis))
            if not any(abs(np.dot(axis, old)) > .999999 for old in axes):
                axes.append(axis)
    frames = []
    for x, y in itertools.permutations(axes[:12], 2):
        y = y-np.dot(x, y)*x
        if np.linalg.norm(y) < .05:
            continue
        y /= np.linalg.norm(y)
        frames.append(np.column_stack((x, y, np.cross(x, y))))
        if len(frames) >= 24:
            break
    return frames


def _iou(a, b):
    overlap = a.common(b).Volume
    union = a.Volume + b.Volume - overlap
    if not math.isfinite(overlap) or union <= 0:
        raise ValueError("Kernel could not compute a finite positive volume union.")
    return min(1., max(0., overlap / union))


def align(candidate, reference, *, samples=1024, tolerance=.1, starts=64, extra=()):
    np, KDTree, Rotation, qmc, _ = _libraries()
    box = reference.BoundBox
    span = max(box.DiagonalLength, tolerance)
    deflection = min(tolerance/4, span/500)
    source, source_n = _samples(candidate, samples, deflection, np, qmc)
    target, target_n = _samples(reference, samples, deflection, np, qmc)
    a, b = _center(candidate, np), _center(reference, np)
    rotations = [("identity", np.eye(3))]
    octahedral = _octahedral(np)
    rotations += [("axis-aligned", r) for r in octahedral]
    ca, cb = _inertia_frame(candidate, np), _inertia_frame(reference, np)
    rotations += [("brep-principal-axes", cb @ r @ ca.T) for r in octahedral]
    _, sa = np.linalg.eigh(np.cov(source.T))
    _, sb = np.linalg.eigh(np.cov(target.T))
    if np.linalg.det(sa) < 0:
        sa[:, 0] *= -1
    if np.linalg.det(sb) < 0:
        sb[:, 0] *= -1
    rotations += [("sampled-principal-axes", sb @ r @ sa.T) for r in octahedral]
    # Analytic surface axes resolve repeated inertia eigenvalues (cubes, shafts).
    af, bf = _axis_frames(candidate, np), _axis_frames(reference, np)
    rotations += [("brep-surface-axes", y @ x.T) for x in af for y in bf]
    draws = qmc.Sobol(3, scramble=False).random_base2(int(math.ceil(math.log2(starts))))[:starts]
    for u, v, w in draws:
        quaternion = [math.sqrt(1-u)*math.sin(2*math.pi*v), math.sqrt(1-u)*math.cos(2*math.pi*v),
                      math.sqrt(u)*math.sin(2*math.pi*w), math.sqrt(u)*math.cos(2*math.pi*w)]
        rotations.append(("sobol", Rotation.from_quat(quaternion).as_matrix()))
    poses = [(name, r, b-r @ a) for name, r in rotations]
    feature = _feature_pose(source, source_n, target, target_n, span, np, KDTree)
    if feature is not None:
        poses.append(("fpfh-fgr", *feature))
    poses += list(extra)
    tree = KDTree(target)

    def error(pose):
        _, r, t = pose
        moved = source @ r.T + t
        return float(np.mean(tree.query(moved)[0]**2) + np.mean(KDTree(moved).query(target)[0]**2))

    unique = {}
    for pose in poses:
        key = tuple(np.round(pose[1].ravel(), 8)) + tuple(np.round(pose[2], 8))
        unique.setdefault(key, pose)
    ranked = sorted(unique.values(), key=lambda p: (error(p), p[0]))
    # Preserve exact moment-derived seeds; point-cloud refinement can make them worse.
    finalists = ranked[:12]
    for name, r, t in ranked[:12]:
        refined = _icp(source, target, r.copy(), t.copy(), np, KDTree, Rotation, span)
        finalists.append((name+"+icp", *refined))
    finalists.sort(key=error)
    best = None
    tested = 0
    for name, rotation, translation in finalists:
        moved = _transform(candidate, rotation, translation)
        agreement = _iou(moved, reference)
        tested += 1
        entry = (agreement, -error((name, rotation, translation)), name, rotation, translation, moved)
        if best is None or entry[:2] > best[:2]:
            best = entry
        if agreement >= 1-1e-9:
            break
    agreement, _, name, rotation, translation, moved = best
    return moved, {"method": name, "rotation": rotation.tolist(), "translation_mm": translation.tolist(),
                   "determinant": float(np.linalg.det(rotation)), "scale": 1.,
                   "starts": len(unique), "kernelPosesTested": tested,
                   "fpfhCorrespondencesUsable": feature is not None,
                   "methods": ["axis-aligned", "brep-principal-axes", "sampled-principal-axes",
                               "brep-surface-axes", "fpfh-fgr", "icp", "sobol"],
                   "globalOptimumGuaranteed": False}, agreement


def _surface(candidate, reference, tolerance, samples, iou):
    if iou >= 1-1e-9:
        return {"precision": 1., "recall": 1., "f1": 1., "method": "exact volume agreement"}
    np, _, _, qmc, _ = _libraries()
    def fraction(a, b):
        points, _ = _samples(a, samples, tolerance/4, np, qmc)
        boundary = Part.makeCompound(b.Faces)
        hits = sum(Part.Vertex(App.Vector(*map(float, point))).distToShape(boundary)[0] <= tolerance for point in points)
        return hits / len(points)
    precision, recall = fraction(candidate, reference), fraction(reference, candidate)
    f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.
    return {"precision": precision, "recall": recall, "f1": f1,
            "method": "area-weighted Sobol surface samples; exact BRep distances", "samplesPerSide": samples}


def topology(shape):
    """Solid Betti counts from welded closed boundary meshes and native shells.

    Periodic BRep seams/poles are not handles. Counting native V-E+F alone is
    incorrect for such faces, so compute Euler characteristic on the boundary.
    """
    np, _, _, _, _ = _libraries()
    handles, voids = 0, 0
    for solid in shape.Solids:
        vertices, triangles = _mesh(solid, max(solid.BoundBox.DiagonalLength/1000, 1e-5), np)
        tolerance = max(solid.getTolerance(1)*2, solid.BoundBox.DiagonalLength*1e-9, 1e-8)
        # Weld seams by actual distance, including neighbours across quantization bins.
        from scipy.spatial import cKDTree
        parent = list(range(len(vertices)))
        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i, j in cKDTree(vertices).query_pairs(tolerance):
            parent[root(j)] = root(i)
        remap = [root(i) for i in range(len(vertices))]
        faces = {tuple(sorted(remap[i] for i in triangle)) for triangle in triangles}
        faces = {face for face in faces if len(set(face)) == 3}
        edges = {}
        used = set()
        for face in faces:
            used.update(face)
            for edge in itertools.combinations(face, 2):
                edges[edge] = edges.get(edge, 0)+1
        if not faces or any(count != 2 for count in edges.values()):
            raise ValueError("Topology count unavailable: tessellated boundary is not closed manifold.")
        chi = len(used)-len(edges)+len(faces)
        cavities = len(solid.Shells)-1
        genus = 1+cavities-chi/2
        if genus < 0 or genus != int(genus):
            raise ValueError("Topology count unavailable: inconsistent boundary Euler characteristic.")
        handles += int(genus)
        voids += cavities
    return {"bodies": len(shape.Solids), "throughHoles": handles, "internalVoids": voids,
            "scope": "Betti b0/b1/b2: throughHoles counts handles, not cylindrical surface faces"}


def _topology_score(candidate, reference):
    got, want = topology(candidate), topology(reference)
    axes = {key: math.exp(-2*abs(math.log((got[key]+1)/(want[key]+1))))
            for key in ("bodies", "throughHoles", "internalVoids")}
    return {"candidate": got, "reference": want, "axes": axes, "score": math.prod(axes.values())}


def _auto_jigs(reference, depth):
    regions = []
    for index, face in enumerate(reference.Faces):
        surface = face.Surface
        kind = type(surface).__name__
        if kind == "Plane":
            axis = surface.Axis
            front = face.extrude(axis*depth)
            back = face.extrude(axis*-depth)
            region = front.fuse(back)
        elif kind == "Cylinder":
            axis, center = surface.Axis, surface.Center
            points = [v.Point for v in face.Vertexes]
            points += list(face.tessellate(max(depth/4, .01))[0])
            values = [(point-center).dot(axis) for point in points]
            if not values or max(values)-min(values) < 1e-8:
                continue
            region = Part.makeCylinder(surface.Radius+depth, max(values)-min(values),
                                       center+axis*min(values), axis)
        else:
            continue
        material = region.common(reference)
        empty = region.cut(reference)
        if material.Volume > 1e-9 and empty.Volume > 1e-9:
            regions.append({"name": f"reference-face-{index+1}", "group": f"face-{index+1}",
                            "material": material, "empty": empty})
    if not regions:
        # Curved freeform parts still get a reference-derived material/clearance jig.
        box = reference.BoundBox
        region = Part.makeBox(box.XLength+2*depth, box.YLength+2*depth, box.ZLength+2*depth,
                              App.Vector(box.XMin-depth, box.YMin-depth, box.ZMin-depth))
        regions.append({"name": "reference-envelope", "group": "envelope",
                        "material": reference, "empty": region.cut(reference)})
    return regions


def _jigs(reference, jigs, depth):
    if jigs is None:
        return _auto_jigs(reference, depth), "automatic reference face collars; geometric proxy, not declared mating intent"
    if not isinstance(jigs, (list, tuple)) or not jigs:
        raise ValueError("jigs must be a non-empty list of keep-in/keep-out regions.")
    regions = []
    for index, jig in enumerate(jigs):
        if not isinstance(jig, dict) or jig.get("kind") not in ("keep-in", "keep-out"):
            raise ValueError("Each jig needs kind='keep-in' or 'keep-out', region, and an opposite-material shell.")
        def read(key):
            value = jig.get(key)
            shape = Part.read(value) if isinstance(value, str) else value
            if shape is None or not hasattr(shape, "Solids") or not shape.Solids or shape.Volume <= 0 or not shape.isValid():
                raise ValueError(f"Jig {index+1} needs a valid positive-volume {key} shape or geometry file.")
            return shape.copy()
        region, shell = read("region"), read("shell")
        material, empty = (region, shell) if jig["kind"] == "keep-in" else (shell, region)
        if material.common(empty).Volume > 1e-7:
            raise ValueError("Jig material and clearance regions overlap.")
        if material.cut(reference).Volume > max(1e-7, material.Volume*1e-6) or empty.common(reference).Volume > max(1e-7, empty.Volume*1e-6):
            raise ValueError("Jig contradicts the reference geometry.")
        regions.append({"name": str(jig.get("name", f"jig-{index+1}")),
                        "group": str(jig.get("group", "interface")), "material": material, "empty": empty})
    return regions, "explicit reference-frame keep-in/keep-out regions with opposite-material shells"


def _interface(candidate, regions, provenance, reference, search_starts):
    np, _, Rotation, qmc, _ = _libraries()
    grouped = {}
    for jig in regions:
        grouped.setdefault(jig['group'], []).append(jig)

    def fit(shape, jig):
        material, empty = jig["material"], jig["empty"]
        filled = shape.common(material).Volume
        blocked = shape.common(empty).Volume
        # The shell detects oversized voids / undersized bosses as well as intrusion.
        material_iou = min(1., max(0., filled/(material.Volume+blocked)))
        clearance_iou = min(1., max(0., (empty.Volume-blocked)/(empty.Volume+material.Volume-filled)))
        agreement = min(material_iou, clearance_iou)
        score = min(1., max(0., (agreement-.80)/.15))
        return {"name": jig["name"], "group": jig["group"], "materialIoU": material_iou,
                "clearanceIoU": clearance_iou, "score": score,
                "missingMaterial_mm3": max(0., material.Volume-filled),
                "blockedClearance_mm3": max(0., blocked)}

    results, scores, poses = [], {}, {}
    box = reference.BoundBox
    window = np.array([box.XLength, box.YLength, box.ZLength])*.01
    draws = qmc.Sobol(6, scramble=False).random_base2(int(math.ceil(math.log2(search_starts+1))))[1:search_starts+1]
    offsets = [(np.zeros(3), np.zeros(3))]
    # Pure-axis trials plus Sobol trials of the combined six-dimensional window.
    for axis in np.eye(3):
        for sign in (-1, 1):
            offsets.append((axis*sign, np.zeros(3)))
            offsets.append((np.zeros(3), axis*sign))
    offsets += [(draw[:3]*2-1, draw[3:]*2-1) for draw in draws]
    for group, jigs in grouped.items():
        center = _center(Part.makeCompound([jig['material'] for jig in jigs]), np)
        best = [fit(candidate, jig) for jig in jigs]
        best_score = min(item['score'] for item in best)
        best_pose = {"translation_mm": [0., 0., 0.], "rotation_xyz_deg": [0., 0., 0.]}
        tested = 1
        if best_score < 1:
            for delta, angles in offsets[1:]:
                rotation = Rotation.from_euler('xyz', angles, degrees=True).as_matrix()
                translation = center-rotation @ center + delta*window
                # Move the candidate into the jig's local bounded registration;
                # the same pose applies to every mating feature in this group.
                moved = _transform(candidate, rotation, translation)
                trial = [fit(moved, jig) for jig in jigs]
                score = min(item['score'] for item in trial)
                tested += 1
                if score > best_score:
                    best, best_score = trial, score
                    best_pose = {"translation_mm": (delta*window).tolist(), "rotation_xyz_deg": angles.tolist()}
                if best_score >= 1:
                    break
        results.extend(best)
        scores[group] = best_score
        poses[group] = {**best_pose, "posesTested": tested}
    return {"score": sum(scores.values())/len(scores), "groups": scores, "jigs": results,
            "provenance": provenance, "poseSearch": {"groupPoses": poses,
                "translationWindow_mm": window.tolist(), "rotationWindow_deg": [1., 1., 1.],
                "method": "bounded proper-rigid axis trials and Sobol; worst feature per group, mean over groups"}}


def _union(solids):
    if not solids:
        raise ValueError("Comparison needs at least one solid.")
    return solids[0].copy() if len(solids) == 1 else solids[0].multiFuse(solids[1:])


def _assembly(candidate_parts, reference_parts, pose, tolerance, angle_tolerance, options):
    np, _, Rotation, _, assignment = _libraries()
    rotation, translation = np.array(pose["rotation"]), np.array(pose["translation_mm"])
    moved = [_transform(part, rotation, translation) for part in candidate_parts]
    scores = np.zeros((len(reference_parts), len(moved)))
    details = {}
    for i, want in enumerate(reference_parts):
        for j, got in enumerate(moved):
            # Shape mismatch cannot obtain a perfect pose score just by sharing a centroid.
            _, local, agreement = align(got, want, **options)
            delta = float(np.linalg.norm(_center(want, np)-_center(got, np)))
            angle = float(Rotation.from_matrix(local["rotation"]).magnitude()*180/math.pi)
            # If geometry already agrees, symmetry makes the identity pose valid.
            centered = got.copy()
            centered.translate(App.Vector(*map(float, _center(want, np)-_center(got, np))))
            if _iou(centered, want) >= agreement-1e-8:
                angle = 0.
            elif angle > 1e-5:
                # Use the smallest equivalent proper rotation for symmetric parts,
                # rather than charging a box for an arbitrary 180-degree PCA flip.
                ca, cb = _inertia_frame(got, np), _inertia_frame(want, np)
                trials = [cb @ r @ ca.T for r in _octahedral(np)]
                trials.sort(key=lambda r: Rotation.from_matrix(r).magnitude())
                for r in trials:
                    degrees = float(Rotation.from_matrix(r).magnitude()*180/math.pi)
                    if degrees >= angle:
                        break
                    t = _center(want, np)-r @ _center(got, np)
                    if _iou(_transform(got, r, t), want) >= agreement-1e-8:
                        angle = degrees
                        break
            d = math.hypot(delta/tolerance, angle/angle_tolerance)
            score = agreement/(1+d)
            scores[i, j] = score
            details[i, j] = {"referencePart": i, "candidatePart": j, "translationError_mm": delta,
                             "rotationError_deg": angle, "errorToleranceUnits": d,
                             "localVolumeIoU": agreement, "score": score}
    rows, columns = assignment(-scores)
    pairs = [details[int(i), int(j)] for i, j in zip(rows, columns)]
    denominator = max(len(reference_parts), len(candidate_parts))
    return {"score": sum(p["score"] for p in pairs)/denominator, "pairs": pairs,
            "missing": [i for i in range(len(reference_parts)) if i not in rows],
            "extra": [j for j in range(len(candidate_parts)) if j not in columns],
            "translationTolerance_mm": tolerance, "rotationTolerance_deg": angle_tolerance,
            "formula": "d=hypot(translation/tolerance, angle/tolerance); localVolumeIoU/(1+d); unmatched=0",
            "globalRigidMotionRemoved": True, "pairing": "Hungarian maximum total score"}


def score(candidate_parts, reference_parts, *, tolerance=.1, angle_tolerance=6., samples=1024,
          starts=64, threshold=.97, jigs=None, jig_depth=None, jig_search_starts=16, mirror=True):
    for name, value in (("tolerance", tolerance), ("angle_tolerance", angle_tolerance), ("threshold", threshold)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number.")
    if threshold > 1 or angle_tolerance > 180:
        raise ValueError("threshold must be <=1 and angle_tolerance <=180 degrees.")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 128 <= samples <= 8192:
        raise ValueError("samples must be an integer from 128 to 8192.")
    if isinstance(starts, bool) or not isinstance(starts, int) or not 8 <= starts <= 1024:
        raise ValueError("starts must be an integer from 8 to 1024.")
    if isinstance(jig_search_starts, bool) or not isinstance(jig_search_starts, int) or not 1 <= jig_search_starts <= 128:
        raise ValueError("jig_search_starts must be an integer from 1 to 128.")
    if not isinstance(mirror, bool):
        raise ValueError("mirror must be a boolean.")
    # Tessellation and OCC booleans cache data on native shapes. Do not let
    # compound construction share the caller's subshapes with those operations.
    candidate_parts = [part.copy() for part in candidate_parts]
    reference_parts = [part.copy() for part in reference_parts]
    if not reference_parts or any(part.ShapeType != "Solid" or not part.isValid() or not part.isClosed() or part.Volume <= 0 for part in reference_parts):
        raise ValueError("Reference must contain valid closed positive-volume solids.")
    if not candidate_parts or any(part.ShapeType != "Solid" or not part.isValid() or not part.isClosed() or part.Volume <= 0 for part in candidate_parts):
        return {"valid": False, "gScore": 0., "score": 0., "passes": False, "threshold": threshold,
                "reason": "Candidate is not a valid closed positive-volume solid."}
    candidate, reference = _union(candidate_parts), _union(reference_parts)
    if not reference.isValid() or not reference.Solids:
        raise ValueError("Reference must contain valid closed solids.")
    if not candidate.isValid() or not candidate.Solids or any(not s.isClosed() for s in candidate.Solids):
        return {"valid": False, "gScore": 0., "score": 0., "passes": False, "threshold": threshold,
                "reason": "Candidate is not a valid closed solid."}
    depth = max(tolerance*2, reference.BoundBox.DiagonalLength*.005) if jig_depth is None else jig_depth
    if isinstance(depth, bool) or not isinstance(depth, (int, float)) or not math.isfinite(depth) or depth <= 0:
        raise ValueError("jig_depth must be a finite positive length in mm.")
    regions, provenance = _jigs(reference, jigs, depth)
    options = {"samples": samples, "tolerance": tolerance, "starts": starts}
    extra = []
    if len(candidate_parts) > 1 or len(reference_parts) > 1:
        np, _, _, _, _ = _libraries()
        # Part-centred seeds keep a missing/extra body from shifting every matched
        # body just because the aggregate centroid or principal axes changed.
        for got in candidate_parts:
            for want in reference_parts:
                if abs(got.Volume/want.Volume-1) > .1:
                    continue
                a, b = _center(got, np), _center(want, np)
                ca, cb = _inertia_frame(got, np), _inertia_frame(want, np)
                for axis_rotation in _octahedral(np):
                    rotation = cb @ axis_rotation @ ca.T
                    extra.append(("assembly-part-axes", rotation, b-rotation @ a))
    aligned, pose, iou = align(candidate, reference, extra=extra, **options)
    surface = _surface(aligned, reference, tolerance, samples, iou)
    shape_score = (iou+surface["f1"])/2
    interfaces = _interface(aligned, regions, provenance, reference, jig_search_starts)
    topo = _topology_score(Part.makeCompound(candidate_parts), Part.makeCompound(reference_parts))
    composite = .4*shape_score+.4*interfaces["score"]+.2*topo["score"]
    handedness = {"status": "not tested", "reflectionAccepted": False}
    if mirror:
        if iou >= 1-1e-9:
            handedness["status"] = "proper rigid match; chirality not distinguishable from this match"
        else:
            np, _, _, _, _ = _libraries()
            reflected = candidate.copy()
            matrix = App.Matrix()
            matrix.A11 = -1
            reflected.transformShape(matrix)
            _, reflected_pose, mirror_iou = align(reflected, reference, **options)
            handedness.update(status="mirror mismatch" if mirror_iou >= 1-1e-6 and mirror_iou-iou > 1e-5 else "no decisive mirror evidence",
                              reflectedVolumeIoU=mirror_iou, properVolumeIoU=iou,
                              diagnosticOnly=True)
    result = {"valid": True, "gScore": composite, "score": composite, "passes": composite >= threshold,
              "scorerVersion": "anthracite.compare/1",
              "threshold": threshold, "weights": {"shape": .4, "interface": .4, "topology": .2},
              "shape": {"score": shape_score, "volumeIoU": iou, "surface": surface},
              "interface": interfaces, "topology": topo, "alignment": pose, "handedness": handedness,
              "tolerance_mm": tolerance, "sampling": {"samples": samples, "deterministic": True},
              "scope": "Finite proper-rigid search; exact BRep volume booleans, sampled surface F1. No scale fitting or mirror acceptance."}
    if len(candidate_parts) > 1 or len(reference_parts) > 1:
        assembly = _assembly(candidate_parts, reference_parts, pose, tolerance, angle_tolerance, options)
        result["assembly"] = assembly
        result["score"] = assembly["score"]
        result["passes"] = composite >= threshold and assembly["score"] >= threshold
    if handedness["status"] == "mirror mismatch":
        result["passes"] = False
    return result
