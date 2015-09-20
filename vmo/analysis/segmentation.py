import librosa
import copy
import scipy
import scipy.linalg as linalg
import scipy.stats as stats
import scipy.sigal as sig
import numpy as np
import sklearn.cluster as sklhc
import scipy.cluster.hierarchy as scihc
from ..VMO.utility import entropy, edit_distance
from collections import OrderedDict
from .analysis import create_selfsim, find_fragments


"""Segmentation algorithms
"""


def segment_by_connectivity(connectivity, median_filter_width, cluster_method, **kwargs):

    obs_len = connectivity.shape[0]
    connectivity = librosa.segment.recurrence_to_lag(connectivity, pad=False)
    connectivity = np.pad(connectivity, [(0, 0), [median_filter_width, median_filter_width]], mode='reflect')
    connectivity = sig.medfilt(connectivity, [1, median_filter_width])
    connectivity = connectivity[:, median_filter_width:-median_filter_width]
    connectivity = librosa.segment.lag_to_recurrence(connectivity)

    connectivity[range(1, obs_len), range(obs_len - 1)] = 1.0
    connectivity[range(obs_len - 1), range(1, obs_len)] = 1.0
    connectivity[np.diag_indices(obs_len)] = 0

    if cluster_method == 'spectral':
        return _seg_by_spectral_single_frame(connectivity=connectivity, **kwargs)
    elif cluster_method == 'spectral_agg':
        return _seg_by_spectral_agg_single_frame(connectivity=connectivity, **kwargs)
    else:
        return _seg_by_spectral_single_frame(connectivity=connectivity, **kwargs)


def _seg_by_structure_feature(oracle):
    connectivity = create_selfsim(oracle, method='rsfx')
    connectivity = librosa.segment.recurrence_to_lag(connectivity, pad=False)
    connectivity = scipy.ndimage.filters.gaussian_filter(connectivity, [2, 9], 0, mode='reflect')
    connectivity = librosa.segment.lag_to_recurrence(connectivity)


    pass


def _seg_by_single_frame(oracle, cluster_method='agglomerative', connectivity='temporal', data='symbol',
                         median_filter_width=9, **kwargs):
    obs_len = oracle.n_states - 1
    median_filter_width = median_filter_width

    if data == 'raw':
        data = np.array(oracle.f_array[1:])
    else:
        data = np.zeros((oracle.n_states - 1, oracle.num_clusters()))
        data[range(oracle.n_states - 1), oracle.data[1:]] = 1

    if connectivity == 'temporal':
        connectivity = np.zeros((obs_len, obs_len))
    elif type(connectivity) == np.ndarray:
        connectivity = connectivity
    else:
        connectivity = create_selfsim(oracle, method=connectivity)

    if cluster_method == 'agglomerative':
        return _seg_by_hc_single_frame(obs_len=obs_len, connectivity=connectivity, data=data, **kwargs)
    else:
        return segment_by_connectivity(connectivity, median_filter_width, cluster_method, **kwargs)


def _seg_by_hc_single_frame(obs_len, connectivity, data, width=9, hier=False, **kwargs):
    _children, _n_c, _n_leaves, parents, distances = \
        sklhc.ward_tree(data, connectivity=connectivity, return_distance=True)

    reconstructed_z = np.zeros((obs_len - 1, 4))
    reconstructed_z[:, :2] = _children
    reconstructed_z[:, 2] = distances

    if 'criterion' in kwargs.keys():
        criterion = kwargs['criterion']
    else:
        criterion = 'distance'

    if hier:
        t_list = range(2, 11)

        label_dict = OrderedDict()
        boundary_dict = OrderedDict()
        criterion = 'maxclust'
        for t in t_list:
            boundaries, labels = _agg_segment(reconstructed_z, t, criterion, width, data)
            label_dict[np.max(labels)+1] = labels
            boundary_dict[np.max(labels)+1] = boundaries
        return boundary_dict, label_dict
    else:
        t = 0.7 * np.max(reconstructed_z[:, 2])
        return _agg_segment(reconstructed_z, t, criterion, width, data)


def _agg_segment(z, t, criterion, width, data):
    label = scihc.fcluster(z, t=t, criterion=criterion)
    k = len(np.unique(label))
    boundaries = find_boundaries(label, width=width)
    while len(boundaries) < k+1 and width > 0:
        width -= 3
        boundaries = find_boundaries(label, width=width-3)
    labels = segment_labeling(data, boundaries, c_method='kmeans', k=k)
    return boundaries, labels


def _seg_by_spectral_single_frame(connectivity, width=9, hier=False, k_min=4, k_max=6):
    graph_lap = normalized_graph_laplacian(connectivity)
    if hier:
        k_max = 10
    eigen_vecs = eigen_decomposition(graph_lap, k=k_max)
    boundaries, labels = clustering_by_entropy(eigen_vecs, k_min=k_min, width=width, hier=hier)
    return boundaries, labels


def _seg_by_spectral_agg_single_frame(connectivity, width=9):
    graph_lap = normalized_graph_laplacian(connectivity)
    eigen_vecs = eigen_decomposition(graph_lap)

    x = librosa.util.normalize(eigen_vecs.T, norm=2, axis=1)
    z = scihc.linkage(x, method='ward')

    t = 0.75 * np.max(z[:, 2])
    return _agg_segment(z, t, criterion='distance', width=width, data=x)


def _seg_by_hc_string_matching(oracle, data='symbol', connectivity=None, **kwargs):
    if data is 'raw':
        data = np.array(oracle.f_array[1:])
    else:
        data = np.zeros((oracle.n_states - 1, oracle.num_clusters()))
        data[range(oracle.n_states - 1), oracle.data[1:]] = 1

    frag_pos, _frag_rsfx = find_fragments(oracle)
    frag_num = len(frag_pos)
    frag_connectivity = np.zeros((frag_num, frag_num))

    fragments = []
    for i, (f, r) in enumerate(zip(frag_pos, _frag_rsfx)):  # f[0]-> pos, f[1]->lrs
        if f[0] == oracle.n_states - 1:
            fragments.append(oracle.data[f[0] - f[1] + 1:])
        else:
            fragments.append(oracle.data[f[0] - f[1] + 1:f[0] + 1])
            if r > 0:
                frag_connectivity[i, r] = 1.0
    frag_connectivity[range(frag_num - 1), range(1, frag_num)] = 1.0

    n_nodes = 2 * frag_num - 1

    _children = []
    distances = np.empty(n_nodes - frag_num)
    frag_indices = range(frag_num)
    _frag = copy.copy(fragments)

    for k in range(frag_num, n_nodes):
        y = [edit_distance(u, v) for (u, v) in zip(_frag[:-1], _frag[1:])]

        flat_ind = np.argmin(y)
        i = flat_ind
        j = flat_ind + 1
        _frag[i] = _frag[i] + _frag[j]
        _frag.pop(j)
        _children.append((frag_indices[i], frag_indices[j]))
        frag_indices[i] = k
        frag_indices.pop(j)
        distances[k - frag_num] = y[flat_ind]

    reconstructed_z = np.zeros((frag_num - 1, 4))
    reconstructed_z[:, :2] = _children
    reconstructed_z[:, 2] = distances

    if 'threshold' in kwargs.keys():
        t = kwargs['threshold']
    else:
        t = 0.1 * np.max(reconstructed_z[:, 2])

    if 'criterion' in kwargs.keys():
        criterion = kwargs['criterion']
    else:
        criterion = 'distance'

    _label = scihc.fcluster(reconstructed_z, t=t, criterion=criterion)
    label = []
    for lab, frag in zip(_label, fragments):
        label.extend([lab] * len(frag))

    boundaries = find_boundaries(label, **kwargs)
    labels = segment_labeling(data, boundaries, c_method='agglomerative', k=0.05)

    return boundaries, labels


def clustering_by_entropy(eigen_vecs, k_min, width=9, hier=False):
    best_score = -np.inf
    best_boundaries = [0, eigen_vecs.shape[1]-1]
    best_n_types = 1
    y_best = eigen_vecs[:1].T

    if hier:
        label_dict = OrderedDict()
        boundary_dict = OrderedDict()
        k_min = 2

    for n_types in range(k_min, 1 + len(eigen_vecs)):
        y = librosa.util.normalize(eigen_vecs[:n_types, :].T, norm=2, axis=1)

        # Try to label the data with n_types
        c = sklhc.KMeans(n_clusters=n_types, n_init=100)
        labels = c.fit_predict(y)

        # Find the label change-points
        boundaries = find_boundaries(labels, width)

        # boundaries now include start and end markers; n-1 is the number of segments
        if len(boundaries) < n_types + 1:
            n_types = len(boundaries)-1

        values = np.unique(labels)
        hits = np.zeros(len(values))

        for v in values:
            hits[v] = np.sum(labels == v)

        hits = hits / hits.sum()
        score = entropy(hits) / np.log(n_types)

        if score > best_score:
            best_boundaries = boundaries
            best_n_types = n_types
            best_score = score
            y_best = y

        if hier:
            labels = segment_labeling(y, boundaries, c_method='kmeans', k=n_types)
            label_dict[n_types] = labels
            boundary_dict[n_types] = boundaries

    # Classify each segment centroid

    labels = segment_labeling(y_best, best_boundaries, c_method='kmeans', k=best_n_types)
    best_labels = labels

    if hier:
        return boundary_dict, label_dict
    else:
        return best_boundaries, best_labels


def segmentation(oracle, method='symbol_agglomerative', **kwargs):
    if oracle:
        if method == 'symbol_agglomerative':
            return _seg_by_single_frame(oracle, cluster_method='agglomerative', **kwargs)
        elif method == 'string_agglomerative':
            return _seg_by_hc_string_matching(oracle, **kwargs)
        elif method == 'symbol_spectral':
            return _seg_by_single_frame(oracle, cluster_method='spectral', **kwargs)
        elif method == 'symbol_spectral_agglomerative':
            return _seg_by_single_frame(oracle, cluster_method='spectral_agg', **kwargs)
        else:
            print "Method unknown. Use spectral clustering."
            return _seg_by_single_frame(oracle, cluster_method='spectral', **kwargs)
    else:
        raise TypeError('Oracle is None')


"""Adapted from Brian McFee`s spectral clustering algorithm for music structural segmentation
https://github.com/bmcfee/laplacian_segmentation
"""


def segment_labeling(x, boundaries, c_method='kmeans', k=5):

    x_sync = librosa.feature.sync(x.T, boundaries)

    if c_method == 'kmeans':
        c = sklhc.KMeans(n_clusters=k, n_init=100)
        seg_labels = c.fit_predict(x_sync.T)
    elif c_method == 'agglomerative':
        z = scihc.linkage(x_sync.T, method='ward')
        t = k * np.max(z[:, 2])
        seg_labels = scihc.fcluster(z, t=t, criterion='distance')
    else:
        c = sklhc.KMeans(n_clusters=k, n_init=100)
        seg_labels = c.fit_predict(x_sync.T)

    return seg_labels


def find_boundaries(frame_labels, width=9):
    frame_labels = np.pad(frame_labels, (width/2, width/2+1), mode='reflect')
    frame_labels = np.array([stats.mode(frame_labels[i:j])[0][0]
                             for (i, j) in zip(range(0, len(frame_labels)-width),
                                               range(width, len(frame_labels)))])
    boundaries = 1 + np.asarray(np.where(frame_labels[:-1] != frame_labels[1:])).reshape((-1,))
    boundaries = np.unique(np.concatenate([[0], boundaries, [len(frame_labels)]]))
    return boundaries


def normalized_graph_laplacian(mat):
    mat_inv = 1. / np.sum(mat, axis=1)
    mat_inv[~np.isfinite(mat_inv)] = 1.
    mat_inv = np.diag(mat_inv ** 0.5)
    laplacian = np.eye(len(mat)) - mat_inv.dot(mat.dot(mat_inv))

    return laplacian


def eigen_decomposition(mat, k=5):  # Changed from 11 to 8 then to 6(7/22)
    vals, vecs = linalg.eig(mat)
    vals = vals.real
    vecs = vecs.real
    idx = np.argsort(vals)

    vals = vals[idx]
    vecs = vecs[:, idx]

    if len(vals) < k + 1:
        k = -1

    return vecs[:, :k].T