# Direção visual — aprovada

**Caráter escolhido:** A · Bancada fria (ver `direcao.html`)
**Status:** aprovada pelo dono em 22/11/2026 · provisória até o F5.0 (se a primeira tela construída pedir ajuste, ajusta-se aqui, não no componente)

---

## As cinco linhas

**Produto** — ferramenta pessoal que mostra, a quem opera opções na B3, o que o mercado está cobrando de volatilidade e onde esse preço saiu da faixa.

**Caráter** — *preciso, contido, frio.*

**Mundo** — bancada de instrumento: escala graduada, papel milimetrado, mostrador analógico com agulha. Luz de bancada, não luz de tela de trading. O grafite é frio (viés azul), não quente.

**Momentos** — os gráficos. Um por tela é o momento e recebe o capricho; os outros são menores e de outra natureza. Fora deles, a tela não tem cor.

**Assinatura** — **a agulha na régua.** Toda métrica que é posição relativa (IV rank, z-VRP, delta, taxa histórica do sinal, dias para liquidar) aparece como escala horizontal com marcador, nunca como número solto. Uma forma, usada em todas.

---

## Tokens

### Cor

| Papel | Valor | Uso |
|---|---|---|
| `bg` | `#101418` | fundo da tela |
| `surface` | `#171d24` | cartão, linha de cadeia, sheet |
| `surface-2` | `#1d242c` | estado ativo, cabeçalho de tabela |
| `line` | `#232c36` | hairline de separação |
| `line-strong` | `#33404e` | borda de foco, divisor de seção |
| `text` | `#e7edf4` | número, título |
| `text-2` | `#8fa0b2` | rótulo, texto secundário |
| `text-3` | `#5d6c7d` | meta, unidade, eixo |
| `accent` | `#e9f2f9` | **o único "brilhante"** |
| `accent-ink` | `#0f1317` | texto sobre o acento |
| `pos` / `neg` | `#7fb08a` / `#c88383` | só variação (+/−), dessaturados |

**Zero matiz fora do gráfico.** O acento não é uma cor, é a única marca clara num campo de cinzas — como a agulha de um instrumento real. Em tema escuro, a ação primária é branco-gelo com texto escuro.

Dentro do gráfico, se duas séries precisarem se distinguir, a regra é **branco contra cinza tracejado**, não duas cores. Uma cor fria (teal dessaturado) só entra como canal funcional se branco/cinza não bastarem — e aí ela significa série, não estilo.

### Tipografia

| Papel | Escolha |
|---|---|
| Interface | sans do sistema (400 e 500; 600 raro) |
| Números | mono, `font-variant-numeric: tabular-nums`, sempre |
| Rótulo | 11 px, `text-2`, **caixa de frase**, sem tracking |
| Corpo | 13,5 px |
| Valor | 16 px |
| Número-herói | 22–28 px |
| Título de tela | 28–30 px |

Hierarquia por **tamanho e cor**, nunca por peso. Nada de caixa alta com tracking, nada de itálico.

### Espaço, raio, movimento

- Grade de **4 px**. Dentro do grupo 8–12; entre seções 24–32.
- Raio: 4 (controle), 6 (cartão), 8 (sheet). Nada de pill.
- Movimento: 120 ms para feedback, 200 ms para entrada; **uma** curva. Só cor, opacidade e transform. Um número que muda faz *tween*; nada entra voando.

---

## Regras que decorrem da direção

1. **Número em coluna.** Todo valor numérico alinha à direita, na mesma coluna, com o mesmo número de decimais na tela.
2. **Régua em vez de número solto** quando a métrica é posicional. O número continua visível — a régua diz onde ele está.
3. **O gráfico ganha a cor; a tela não.** Se os gráficos ficarem cinzas também, o resultado é frieza sem recompensa. Um gráfico por tela é o momento; os demais são listas com barras finas ou tabelas curtas.
4. **Meta é texto quieto**, mono e muted — nunca pill colorida.
5. **Zero emoji** na interface. Ícone só onde reduz leitura (seta, fechar, voltar), num conjunto só, 16 px, traço 1,5.
6. **Contraste**: texto `text-2` sobre `surface` ≥ 4,5:1. O acento sobre `surface` só como marca, nunca como texto corrido.

## Proibido

Gradiente (inclusive em texto), glow, sombra colorida, blob, borda neon, badge colorido, pill em tudo, caixa alta com tracking, ícone decorativo em rótulo, sparkline dentro de card de estatística, `animate-pulse` em coisa que não é ao vivo, `hover:scale`, `transition-all`.

## O teste do template

Se trocássemos "opções B3" por outro produto, a tela continuaria servindo?

**Não.** As réguas medem posição relativa de volatilidade, o léxico é de instrumento (escala, faixa, medição), a assinatura é a agulha e a única cor viva está no smile. Trocar o produto quebraria as três coisas ao mesmo tempo.

## O risco, e a mitigação

**Austeridade.** Sem cor, não há onde esconder: erro de espaçamento, número desalinhado e hierarquia fraca ficam visíveis.

Mitigação, em ordem: (a) os gráficos carregam a cor; (b) números grandes, tabulares, bem compostos — é onde o capricho aparece; (c) espaço generoso **entre** grupos e apertado **dentro** deles, para a tela respirar sem ficar vazia.

## O que isso substitui

Os mockups em `docs/` deixam de ser alvo visual e passam a ser referência de estrutura e conteúdo. `direcao.html` continua sendo o registro da escolha.
