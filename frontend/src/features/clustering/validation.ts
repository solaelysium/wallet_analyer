import type { ClusteringRequest } from '../../api/types'

export function validateClustering(input: ClusteringRequest): string[] {
  const errors: string[] = []
  if (!input.feature_version) errors.push('Необходимо выбрать версию признаков.')
  if (input.feature_names.length < 2) errors.push('Выберите не менее двух признаков.')
  if (input.reducer_components < 2 || input.reducer_components > 20) {
    errors.push('Число измерений после снижения должно быть от 2 до 20.')
  }
  if (input.reducer === 'umap') {
    if (input.umap_neighbors < 2 || input.umap_neighbors > 200) {
      errors.push('Число соседей UMAP должно быть от 2 до 200.')
    }
    if (input.umap_min_dist < 0 || input.umap_min_dist > 1) {
      errors.push('Минимальное расстояние UMAP должно быть от 0 до 1.')
    }
  }
  if (
    input.winsorize
    && (input.winsor_lower < 0
      || input.winsor_upper > 1
      || input.winsor_lower >= input.winsor_upper)
  ) {
    errors.push('Указаны некорректные границы винзоризации.')
  }
  if (input.algorithm === 'kmeans') {
    if (input.n_clusters < 2 || input.n_clusters > 50) {
      errors.push('Число кластеров KMeans должно быть от 2 до 50.')
    }
  } else {
    if (input.min_cluster_size < 2 || input.min_cluster_size > 1000) {
      errors.push('Минимальный размер кластера HDBSCAN должен быть от 2 до 1000.')
    }
    if (input.min_samples !== null && (input.min_samples < 1 || input.min_samples > 1000)) {
      errors.push('Минимальное число образцов HDBSCAN должно быть от 1 до 1000.')
    }
  }
  return errors
}
