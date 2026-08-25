import { describe, expect, it } from 'vitest'
import type { ClusteringRequest } from '../../api/types'
import { validateClustering } from './validation'

const validRequest: ClusteringRequest = {
  algorithm: 'hdbscan',
  reducer: 'umap',
  feature_version: 'wallet_features.v1',
  feature_names: ['balance', 'transactions'],
  n_clusters: 8,
  min_cluster_size: 20,
  min_samples: 5,
  random_state: 42,
  umap_neighbors: 15,
  umap_min_dist: 0.1,
  umap_metric: 'cosine',
  reducer_components: 5,
  scaler: 'robust',
  winsorize: true,
  winsor_lower: 0.01,
  winsor_upper: 0.99,
  log_transform: false,
  cluster_selection_method: 'eom',
}

describe('clustering validation', () => {
  it('accepts a valid configuration', () => {
    expect(validateClustering(validRequest)).toEqual([])
  })

  it('reports invalid feature and algorithm parameters', () => {
    expect(
      validateClustering({
        ...validRequest,
        feature_names: ['balance'],
        umap_neighbors: 1,
        min_cluster_size: 1,
        min_samples: 1001,
      }),
    ).toHaveLength(4)
  })
})
