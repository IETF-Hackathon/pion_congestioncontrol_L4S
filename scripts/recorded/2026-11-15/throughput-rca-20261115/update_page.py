#!/usr/bin/env python3
"""Insert the validated RCA into the existing single-page IETF proposal.

The bundled report renderer supplies escaped findings and evidence markup.
Only this marked block and its scoped styles are replaced on later updates.
"""
from pathlib import Path
from html import escape
import json
import re

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2]


def scoped_fragment(markup):
    """Namespace generated evidence anchors and classes to preserve page styling."""
    markup=markup.replace('href="#e-','href="#rca-e-').replace('id="e-','id="rca-e-')
    markup=re.sub(r'class="([^"]+)"',lambda match:'class="'+' '.join('rca-'+name for name in match[1].split())+'"',markup)
    return markup.replace('<h3>','<h4>').replace('</h3>','</h4>').replace('<th>','<th scope="col">')


def main():
    """Keep historical results intact while adding a clearly scoped throughput RCA."""
    record=json.loads((ROOT/'results/preview/pacing-throughput-rca-20261115.json').read_text())
    metrics=json.loads((ROOT/'results/preview/pacing-throughput-rca-20261115-metrics.json').read_text())
    rendered=(BASE/'renderer-output.html').read_text()
    findings=scoped_fragment(re.search(r'<section><h2>Findings</h2>(.*?)</section>',rendered,re.S)[1])
    evidence=scoped_fragment(re.search(r'<section><h2>Evidence register</h2>(.*?)</section>',rendered,re.S)[1])
    # Keep the model explicitly distinct from lab results and use only typed
    # numeric fields in the small summary table; prose is escaped by renderer.
    model_rows='\n'.join(f"<tr><td>{int(row['feedback_interval_ms'])} ms</td><td>{float(row['mean_accounted_mbps']):.2f} Mbps</td></tr>" for row in metrics['illustrative_model']['cases'])
    root_cause=escape(record['rca']['root_cause']['summary'])
    block=f'''<!-- throughput-rca:start -->
        <div class="panel body-copy throughput-rca" id="pacing-throughput-rca" style="margin-top:20px">
          <h3 id="pacing-rca-title">Throughput RCA · why Pion Prague sends less than GCC</h3>
          <p><strong>Assessment: sender limits confirmed; window/feedback mechanism provisional.</strong> This RCA applies to the current Pion integration and fixed-source runs. In the fresh repeat, the median applied Prague target was <strong>25.06 Mbps</strong>, while UDP media acceptance averaged <strong>13.70 Mbps</strong> over the same sender interval. Persistent window stalls and source backpressure are directly recorded. A low configured Prague rate cap does not explain that gap.</p>
          <p><strong>Comparison correction:</strong> both modes used CCFB, but their cadence was different: approximately <strong>20 ms for Prague</strong> and <strong>100 ms for GCC</strong>, confirmed in saved traces and pinned code. GCC also uses an asynchronous packet queue, a 5 ms pacer cycle and a 1.5× allowance after estimator updates. The original results compare these complete transport modes, including those policy differences.</p>
          <div class="table-wrap" tabindex="0" role="region" aria-label="Aligned sender evidence for the throughput RCA">
            <table>
              <thead><tr><th scope="col">Fresh-repeat sender evidence</th><th scope="col">Value</th><th scope="col">Interpretation</th></tr></thead>
              <tbody>
                <tr><td>Aligned snapshot interval</td><td>28.221 s</td><td>15 Nov 2026, 11:37:54.766–11:38:22.987 PST; all counter deltas below use these same snapshots.</td></tr>
                <tr><td>Applied target in periodic samples</td><td>Median 25.06 Mbps; range 20.40–26.80</td><td>Sampled controller permission remains above actual output.</td></tr>
                <tr><td>UDP accepted / accounted RTP rate</td><td>13.70 / 14.02 Mbps</td><td>Different byte bases explain about 0.32 Mbps, not the full target/output gap. UDP success is not a NIC timestamp.</td></tr>
                <tr><td>Pacing waits</td><td>14.72 s · 52.2%</td><td>Waiting for byte credit is normal rate enforcement; this time is not automatically wasted or erroneous.</td></tr>
                <tr><td>Window waits</td><td>11.83 s · 41.9%</td><td>The sender repeatedly waits for acknowledgments to free in-flight space.</td></tr>
                <tr><td>Pre-CCFB headroom</td><td>1,474 / 1,487 arrivals · 99.13%</td><td>These snapshots lacked space for the largest 1,244-byte accounted packet. This is a feedback-arrival count, not percent of elapsed time.</td></tr>
                <tr><td>Actual / nominal source frame rate</td><td>33.17 / 60 frames/s</td><td>Synchronous packet gating blocks frame submission; the file’s nominal rate is not its achieved production rate.</td></tr>
              </tbody>
            </table>
          </div>
          <figure style="margin:18px 0">
            <a href="results/preview/pacing-throughput-rca-20261115.png" aria-label="Open the sender throughput RCA chart at full size"><img src="results/preview/pacing-throughput-rca-20261115.png" alt="Applied Prague allowance stays around 25 Mbps while accepted UDP output is around 14 Mbps; aligned sender counters show 14.72 seconds pacing wait and 11.83 seconds window wait" style="display:block;width:100%;height:auto"></a>
            <figcaption>Measured sender counters localize a substantial constraint before the bottleneck. The remaining 1.67 s includes other processing and source activity; it is not a CPU utilization measurement. Waits are classified by the gate that blocked at entry.</figcaption>
          </figure>
          <h4>Leading mechanism: the window fills before batched feedback releases it</h4>
          <p>{root_cause}</p>
          <p>At 25 Mbps, one 20 ms reporting period represents <strong>62,500 bytes</strong> of traffic, close to the observed <strong>72,000-byte median window</strong>. Median smoothed RTT was <strong>21.97 ms</strong>; receiver report hold averaged another <strong>10.63 ms</strong>. The adapter removes that hold when estimating RTT, yet it still waits for the report before freeing in-flight bytes. Almost every observed CCFB arrival released space in a near-full window.</p>
          <p><strong>Why the new pacer does not catch up automatically:</strong> native 250 µs burst sizing gives one packet at these rates, and the byte-credit cap was 1,244 bytes. Credit earned during a long window stall is clamped to that cap. After feedback, the writer resumes normal packet pacing rather than releasing a window-sized catch-up burst. That preserves burst bounds but makes feedback/window integration important.</p>
          <p><strong>Why GCC can deliver more:</strong> its source write returns after enqueuing; its worker sends multiple packets from a budget every 5 ms, without this Prague byte-window gate. After an estimator update, the GCC pacing allowance is 1.5× the estimate; the initial constructor value is unmultiplied. Queue growth and more aggressive scheduling can increase offered load. Actual GCC queue age was not logged, so its contribution to the observed delay cannot be isolated from these captures.</p>
          <details>
            <summary>Formulas and an offline mechanism check · simulated results</summary>
            <pre style="white-space:pre-wrap;overflow-wrap:anywhere">Prague admission: enough byte credit AND (inflight + packet_bytes ≤ window_bytes)
Native window, approximately: 1.03 × rate_bytes_per_second × corrected_RTT_seconds + packet rounding
Actual ACK-credit return: network RTT + receiver report hold, arriving in batches
GCC budget after a target update: elapsed_ms × (1.5 × estimator_bps) / 8000 bytes</pre>
            <p>An idealized discrete-event model holds the rate at 25 Mbps, the window at 72,000 bytes, packets at 1,244 accounted bytes and symmetric RTT at 22 ms. Timers are perfect, the source is always ready, and there is no CE, loss or bandwidth bottleneck. Only the report interval varies; four initial report phases are sampled.</p>
            <div class="table-wrap" tabindex="0" role="region" aria-label="Illustrative model results, not physical testbed measurements">
              <table><thead><tr><th scope="col">Modeled report interval</th><th scope="col">Modeled accounted output</th></tr></thead><tbody>{model_rows}</tbody></table>
            </div>
            <p><strong>Interpretation:</strong> the 20 ms case produces 14.18 Mbps despite perfect timers and a fixed 25 Mbps allowance. Removing the window in the same model yields about 25 Mbps. This demonstrates a possible mechanism, not a measured fix, native-controller simulation or recommendation to disable the window. The model has no bottleneck capacity limit and does not predict these rates through the lab’s assumed 20 Mbps path.</p>
          </details>
          <h4>What to validate next</h4>
          <ol>
            <li>Make feedback cadence explicit and recorded on both modes. Compare Prague at 20 ms versus a shorter interval while keeping the source and native controller/window/burst policies unchanged; record their time-varying outputs. Then match GCC’s report cadence for a fairer comparison.</li>
            <li>Review RTT, report-hold and window integration against the reference controller. Keep window and burst safety; validate changes rather than widening the window or multiplying targets to force throughput.</li>
            <li>Record bounded per-packet admission/UDP timing and GCC queue age. Apply a common bounded frame-age/source adaptation policy before claiming media-quality or encoder improvements.</li>
            <li>Reconcile feedback tails and peer-close handling, and obtain live bottleneck settings plus controlled background-traffic conditions.</li>
          </ol>
          <p><strong>Still unresolved:</strong> three CE marks are beyond the fresh repeat’s last recorded sender state, the receiver logged a closed-pipe shutdown error, and shaping mode/capacity remain unverified. Lower offered load can itself reduce queueing. The current low transit and zero unmatched packets do not yet demonstrate full-utilization L4S performance.</p>
          <details><summary>Confirmed findings, provisional cause and supporting references</summary>{findings}</details>
          <details><summary>Evidence register and limitations</summary>{evidence}</details>
          <p style="margin-top:16px"><a href="results/preview/pacing-throughput-rca-20261115.json">Sourced RCA record</a> · <a href="results/preview/pacing-throughput-rca-20261115-metrics.json">Derived metrics and model assumptions</a> · <a href="results/preview/new-pion-v2-repeat-25m-20261115T193712Z.md">Fresh-repeat report</a> · <a href="internal/prague/controller.go">Prague adapter</a> · <a href="cmd/pion-l4s/feedback.go">Receiver feedback loop</a></p>
        </div>
        <!-- throughput-rca:end -->'''
    path=ROOT/'index.html';page=path.read_text()
    start='<!-- throughput-rca:start -->';end='<!-- throughput-rca:end -->'
    if start in page:
        a=page.index(start);b=page.index(end,a)+len(end);page=page[:a]+block+page[b:]
    else:
        target=page.index('\n        <div class="steps" style="margin-top:16px">',page.index('id="pacing-results"'))
        page=page[:target]+'\n        '+block+'\n'+page[target:]
    style='''/* throughput-rca:styles:start */
    .throughput-rca h4 { margin:20px 0 8px; font-size:1.06rem; }
    .throughput-rca details { margin-top:16px; }
    .throughput-rca summary { cursor:pointer; font-weight:700; }
    .throughput-rca .rca-finding { margin-top:14px; padding-top:12px; border-top:1px solid #dbe3e8; }
    .throughput-rca .rca-badge { display:inline-block; padding:2px 8px; border-radius:12px; font-size:.8rem; background:#edf2f5; }
    .throughput-rca .rca-confirmed, .throughput-rca .rca-observed_value { background:#e5f3ea; color:#155d39; }
    .throughput-rca .rca-provisional, .throughput-rca .rca-partial { background:#fff1d6; color:#825514; }
    .throughput-rca .rca-refs, .throughput-rca small { font-size:.83rem; color:#526775; }
    .throughput-rca .rca-table-scroll { max-width:100%; overflow-x:auto; margin-top:14px; }
    .throughput-rca .rca-table-scroll table { min-width:880px; }
    .throughput-rca .rca-table-scroll td { min-width:150px; overflow-wrap:anywhere; }
    .throughput-rca .rca-table-scroll td:nth-child(3), .throughput-rca .rca-table-scroll td:nth-child(4) { min-width:250px; }
    .throughput-rca .rca-table-scroll p { white-space:pre-wrap; }
    /* throughput-rca:styles:end */'''
    marker='/* throughput-rca:styles:start */'
    if marker in page:
        a=page.index(marker);b=page.index('/* throughput-rca:styles:end */',a)+len('/* throughput-rca:styles:end */');page=page[:a]+style+page[b:]
    else:
        page=page.replace('</style>',style+'\n  </style>',1)
    # A short contextual link keeps the new section discoverable from the runs.
    navigation='<p><a href="#pacing-throughput-rca">Read the throughput RCA: sender stalls, feedback cadence and GCC differences</a></p>'
    if navigation not in page:
        label='<h3>New pacing results against the saved GCC baseline</h3>'
        page=page.replace(label,label+'\n            '+navigation,1)
    assert page.count('id="pacing-throughput-rca"')==1
    path.write_text(page)
    print('Updated the existing index.html with one scoped RCA section')


if __name__=='__main__':
    main()
