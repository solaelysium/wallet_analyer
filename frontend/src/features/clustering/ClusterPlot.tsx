import Plotly from 'plotly.js-gl2d-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import { Download, Focus } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ClusterProfile, ClusteringJob } from '../../api/types'

const colors = ['#6574d8', '#53a68b', '#e08c65', '#9b72cf', '#d1a43c', '#dd6f92', '#4b9dc3']
const Plot = createPlotlyComponent(Plotly)

export function ClusterPlot({ job }: { job: ClusteringJob }) {
  const [graph, setGraph] = useState<Plotly.PlotlyHTMLElement | null>(null)
  const [selectedCluster, setSelectedCluster] = useState<number | null>(null)
  const traces = useMemo<Partial<Plotly.PlotData>[]>(() => {
    const grouped = new Map<number, NonNullable<ClusteringJob['points']>>()
    job.points?.forEach((point) => {
      const group = grouped.get(point.cluster) ?? []
      group.push(point)
      grouped.set(point.cluster, group)
    })
    return Array.from(grouped.entries())
      .sort(([left], [right]) => left - right)
      .map(([cluster, points], index) => ({
        type: 'scattergl',
        mode: 'markers',
        name: cluster === -1 ? 'Шум' : `Кластер ${cluster}`,
        x: points.map((point) => point.x),
        y: points.map((point) => point.y),
        text: points.map((point) => point.address),
        customdata: points.map((point) => [point.cluster, point.probability ?? null]),
        hovertemplate:
          '<b>%{text}</b><br>x: %{x:.3f}<br>y: %{y:.3f}<br>Кластер: %{customdata[0]}<br>Вероятность: %{customdata[1]:.2f}<extra></extra>',
        marker: {
          color: cluster === -1 ? '#a7afbd' : colors[index % colors.length],
          size: cluster === -1 ? 5 : 7,
          opacity: cluster === -1 ? 0.45 : 0.78,
        },
      }))
  }, [job.points])

  const profile: ClusterProfile | undefined = job.profiles?.find((item) => item.cluster === selectedCluster)

  function exportGraph(format: 'png' | 'svg') {
    if (!graph) return
    void Plotly.downloadImage(graph, {
      format,
      filename: `${job.name.toLowerCase().replaceAll(' ', '-')}-${job.id}`,
      width: 1400,
      height: 900,
    })
  }

  return (
    <div className="cluster-result">
      <div className="plot-toolbar">
        <div><Focus size={16} /><span>Колесо — масштаб · перетаскивание — панорама · рамка или лассо — выбор</span></div>
        <div>
          <button className="button secondary small" type="button" onClick={() => exportGraph('png')}>
            <Download size={14} /> PNG
          </button>
          <button className="button secondary small" type="button" onClick={() => exportGraph('svg')}>
            <Download size={14} /> SVG
          </button>
        </div>
      </div>
      <div className="plot-and-profile">
        <Plot
          data={traces}
          layout={{
            autosize: true,
            margin: { l: 48, r: 16, t: 24, b: 48 },
            paper_bgcolor: '#ffffff',
            plot_bgcolor: '#fbfcff',
            font: { family: 'Inter, system-ui, sans-serif', color: '#536071' },
            hovermode: 'closest',
            dragmode: 'lasso',
            xaxis: { gridcolor: '#edf0f5', zeroline: false, title: { text: 'Проекция 1' } },
            yaxis: { gridcolor: '#edf0f5', zeroline: false, title: { text: 'Проекция 2' } },
            legend: { orientation: 'h', y: -0.18 },
            uirevision: job.id,
          }}
          config={{
            responsive: true,
            scrollZoom: true,
            displaylogo: false,
            modeBarButtonsToRemove: ['toImage'],
          }}
          useResizeHandler
          className="cluster-plot"
          onInitialized={(_, element) => setGraph(element as Plotly.PlotlyHTMLElement)}
          onUpdate={(_, element) => setGraph(element as Plotly.PlotlyHTMLElement)}
          onClick={(event) => {
            const cluster = event.points[0]?.customdata
            if (Array.isArray(cluster)) setSelectedCluster(Number(cluster[0]))
          }}
          onSelected={(event) => {
            const cluster = event?.points[0]?.customdata
            if (Array.isArray(cluster)) setSelectedCluster(Number(cluster[0]))
          }}
        />
        <aside className="profile-panel">
          <span className="eyebrow">Профиль кластера</span>
          {profile ? (
            <>
              <h3>{profile.cluster === -1 ? 'Шум' : `Кластер ${profile.cluster}`}</h3>
              <div className="profile-metrics">
                <div><strong>{profile.size.toLocaleString('ru-RU')}</strong><span>Кошельков</span></div>
                <div><strong>{(profile.share * 100).toFixed(1)}%</strong><span>Доля набора</span></div>
              </div>
              <h4>Средние значения признаков</h4>
              <div className="compact-list">
                {Object.entries(profile.means).map(([name, value]) => (
                  <div key={name}><span>{name}</span><strong>{value.toLocaleString('ru-RU', { maximumFractionDigits: 3 })}</strong></div>
                ))}
              </div>
            </>
          ) : (
            <p>Выберите точку или кластер с помощью лассо, чтобы изучить его профиль.</p>
          )}
        </aside>
      </div>
    </div>
  )
}
