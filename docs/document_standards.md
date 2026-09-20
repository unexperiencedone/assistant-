# World-Class Writing, Diagramming and Document Standards

This specification defines the universal quality, structural, and visual standards for any document produced by Nova or its agents (Claude Code, Antigravity, local LLMs). Follow these rules whenever creating articles, technical reports, blog posts, whitepapers, executive summaries, or Word/PDF exports.

---

## 1. Document Archetypes and Structural Blueprints

Every writing piece must strictly match its target archetype:

### A. Technical and Engineering Articles
* **Goal**: Explain complex architectures, algorithms, or systems with technical rigor and clarity.
* **Structure**:
  1. **Title & Abstract / Hook**: Problem context and high-level premise in 2-3 sentences.
  2. **Core Problem Statement**: Specific challenges, bottlenecks, or design constraints.
  3. **High-Level System Topology**: Architecture overview with a system block diagram.
  4. **Component Deep Dive**: Detailed breakdown of modules, data contracts, and workflows.
  5. **Data Flow & Interactions**: Sequence diagram or pipeline flowchart illustrating execution.
  6. **Trade-offs, Edge Cases & Performance**: Quantitative analysis, limitations, and mitigations.
  7. **Conclusion & Practical Implementation**: Actionable takeaway or next steps.

### B. Formal Technical & Progress Reports
* **Goal**: Deliver verifiable findings, project milestones, performance metrics, and recommendations.
* **Structure**:
  1. **Metadata Header**: Document Title, Author/Team, Date, Version, Status.
  2. **Executive Summary**: Key accomplishments, critical findings, and essential metrics in a callout block.
  3. **Scope & Methodology**: What was evaluated, testbeds, data sources, and assumptions.
  4. **Detailed Findings & Architectural Analysis**: Numbered sections with embedded visual evidence (Figure 1, Table 1).
  5. **Risk Assessment & Mitigation Matrix**: Structured table of risks, impact, and controls.
  6. **Action Plan / Recommendations**: Prioritized checklist or milestone timeline.
  7. **Appendix / References**: Supporting logs, schemas, or raw benchmarks.

### C. Developer & Thought-Leadership Blog Posts
* **Goal**: Engage, educate, and persuade a broader technical audience with approachable authority.
* **Structure**:
  1. **Catchy, Clear Headline & Hook**: Relatable pain point or surprising insight.
  2. **TL;DR Box**: 2-sentence summary right upfront.
  3. **Conceptual "Hero" Diagram**: An intuitive visual mental model early (within the first 2-3 paragraphs).
  4. **Step-by-Step Narrative / Walkthrough**: Clear code snippets, real-world examples, and analogies.
  5. **Before-and-After Comparison**: Table or side-by-side workflow diagram showing the contrast.
  6. **Summary & Call to Action**: Key lessons and actionable recommendations.

### D. Executive Briefs & Whitepapers
* **Goal**: Inform strategic decision-makers with concise business impact and technical justification.
* **Structure**:
  1. **The Strategic Challenge**: Market/operational problem and business costs.
  2. **The Proposed Solution**: Strategic approach and high-level conceptual diagram.
  3. **Impact & ROI Metrics**: Clear summary table of performance, latency, or cost benefits.
  4. **Roadmap & Resource Requirements**: Phased rollout schedule.

---

## 2. Diagram Placement and Architecture Rules (The Golden Rules)

Visuals are not decorative—they are critical cognitive anchors. Every diagram must satisfy these rules:

### Rule 1: The Principle of Proximity (Introduce Before Illustrating)
* **Never drop an orphaned diagram.** A diagram must never appear without prior textual introduction.
* Always contextualize the diagram in the preceding paragraph:
  > *"As illustrated in Figure 2 below, the request pipeline passes through a local intent filter before dispatching to external agent backends..."*
* Follow the visual immediately with an explanation of its key components.

### Rule 2: Standalone Comprehension (The Self-Sufficiency Test)
* A reader must be able to understand 80% of the concept by reading **only** the diagram and its caption.
* Avoid cryptic node labels like `Node A` or `Box 1`. Use descriptive names with functional roles: e.g., `EventBus (Pub/Sub Router)`.

### Rule 3: Formatted Captions with Takeaways
* Captions must never be just labels (`Figure 1: Architecture`).
* **Format**: `Figure [N]: [Title] — [1-2 sentence descriptive caption stating the key takeaway].`
* *Example*: `Figure 1: Multi-Agent Triage Pipeline — Spoken user input is classified locally in under 5 milliseconds, routing urgent control intents away from LLM sessions to eliminate latency and API costs.`

### Rule 4: Visual Rhythm and Pacing
* Maintain a cognitive break every **300 to 500 words**: a diagram, comparison table, code block, or callout box.
* Never stack two complex diagrams back-to-back without transitional text explaining the relationship between them.

---

## 3. Diagram Selection Matrix

Choose the diagram type that precisely matches the concept:

| Purpose / Concept | Best Diagram Type | Standard Syntax / Tool |
|---|---|---|
| **System Architecture / Components** | Block Diagram / C4 Container | Mermaid `flowchart TD` or `flowchart LR` |
| **Request / API / Protocol Flow** | Sequence Diagram | Mermaid `sequenceDiagram` |
| **Lifecycle / State Transitions** | State Machine | Mermaid `stateDiagram-v2` |
| **Data Models / Schemas** | Entity Relationship Diagram | Mermaid `erDiagram` |
| **Process / Workflow / Decision Tree**| Flowchart with Decision Diamonds| Mermaid `flowchart TD` |
| **Feature / Option Comparison** | Formatted Comparison Table | Markdown Table with ✔ / ✖ / notes |
| **Metric Comparison / Benchmarks** | Bar / Line Chart | Mermaid `xychart-beta` or structured table |
| **Terminal / CLI Documentation** | Clean Unicode Box-Drawing | Unicode characters (`┌─┐│└─┘`) |

---

## 4. Visual Aesthetics & Design Formatting

World-class technical documents follow professional design constraints:

1. **Restrained Color Palette**:
   * Use a primary dark slate (`#1E293B`) for borders/text.
   * Use subtle light fills (`#F8FAFC`, `#F1F5F9`) for node backgrounds.
   * Use a single primary accent color (`#2563EB` blue or `#0D9488` teal) to highlight the critical path.
   * Avoid rainbow diagrams where every box has an arbitrary color.
2. **Directional Consistency**:
   * Flowcharts should flow either strictly **Top-to-Bottom (`TD`)** or **Left-to-Right (`LR`)**.
   * Timelines and sequential pipelines should always flow **Left-to-Right**.
3. **Typographic Hierarchy**:
   * `# Title` (Document title, once at top)
   * `## Heading 2` (Major conceptual sections)
   * `### Heading 3` (Sub-systems, components, or subsections)
   * Callouts / Highlights: Blockquotes (`> [!NOTE]`, `> [!IMPORTANT]`, `> [!TIP]`)
   * Key terms: **Bold** on first introduction, followed by concise definition.

---

## 5. Export and Medium Integration

* **Markdown / Web / Canvas**: Use native fenced Mermaid blocks or clean Markdown tables.
* **Microsoft Word (`.docx`) via `office.py`**:
  * Use Markdown formatting (`#`, `##`, `###`, bullets, numbers).
  * Insert diagrams using `![Figure N: Title — Caption](path/to/image.png)`.
  * The Word automation engine embeds the graphic inline, centers it, and styles the caption directly below.
* **Plain-text Fallback**: When tools or viewers do not support image rendering, provide a clean ASCII/Unicode diagram structure so the information remains completely accessible.
