"""Métricas do F1b: `metrics_series` + `metrics_underlying` por sessão.

O pacote inteiro é o pipeline por data do contrato `docs/contratos/onda-fg.md`
§4.5. As fórmulas moram em `b3o/derive/` (puras); aqui ficam a aritmética de
smile por série/papel, o estado rolante das janelas de 252 sessões e o I/O via
`QueryService` (leitura) e `store/metrics.py` (escrita). `duckdb` é proibido
neste pacote (teste de arquitetura): nenhuma consulta SQL nasce fora de
`store/`/`query/`.
"""

from b3o.metrics.pipeline import MetricsSummary, run_metrics

__all__ = ["MetricsSummary", "run_metrics"]
