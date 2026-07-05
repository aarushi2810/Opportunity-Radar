/**
 * Opportunity Radar — Dashboard Interactivity
 * SSE connection, alert rendering, feedback, pipeline status polling, auth
 */

(function () {
    'use strict';

    // ── Auth State ───────────────────────────────────────────────────────────
    const auth = {
        token: localStorage.getItem('or_token') || null,
        user: JSON.parse(localStorage.getItem('or_user') || 'null'),
        mode: 'login', // 'login' | 'register'
    };

    function authHeaders() {
        return auth.token
            ? { 'Content-Type': 'application/json', 'Authorization': `Bearer ${auth.token}` }
            : { 'Content-Type': 'application/json' };
    }

    // ── App State ────────────────────────────────────────────────────────────
    const state = {
        alerts: [],
        filteredAlerts: [],
        seenAlertIds: new Set(),
        signalCounts: { BULLISH: 0, BEARISH: 0, WATCH: 0, NEUTRAL: 0 },
        connected: false,
    };

    // ── DOM ──────────────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const alertsFeed = $('alertsFeed');
    const emptyState = $('emptyState');
    const alertCountBadge = $('alertCount');

    // ── Auth UI ──────────────────────────────────────────────────────────────
    window.openAuthModal = function (mode) {
        auth.mode = mode;
        $('authModal').style.display = 'flex';
        $('authError').style.display = 'none';
        $('authPassword').value = '';
        if (mode === 'register') {
            $('modalTitle').textContent = 'Create Account';
            $('registerFields').style.display = 'block';
            $('authSubmitBtn').textContent = 'Create Account';
            $('formToggleText').textContent = 'Already have an account?';
            $('formToggleBtn').textContent = 'Sign In';
        } else {
            $('modalTitle').textContent = 'Sign In';
            $('registerFields').style.display = 'none';
            $('authSubmitBtn').textContent = 'Sign In';
            $('formToggleText').textContent = "Don't have an account?";
            $('formToggleBtn').textContent = 'Register';
        }
    };

    window.closeAuthModal = function () {
        $('authModal').style.display = 'none';
    };

    window.toggleAuthMode = function () {
        openAuthModal(auth.mode === 'login' ? 'register' : 'login');
    };

    window.submitAuth = async function () {
        const email = $('authEmail').value.trim();
        const password = $('authPassword').value;
        const name = $('authName') ? $('authName').value.trim() : '';
        const errEl = $('authError');
        const btn = $('authSubmitBtn');

        errEl.style.display = 'none';
        btn.disabled = true;
        btn.textContent = 'Please wait...';

        try {
            const url = auth.mode === 'register' ? '/api/auth/register' : '/api/auth/login';
            const body = auth.mode === 'register'
                ? { name, email, password }
                : { email, password };

            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();

            if (!res.ok) {
                errEl.textContent = data.detail || 'Authentication failed';
                errEl.style.display = 'block';
                return;
            }

            auth.token = data.token;
            auth.user = data.user;
            localStorage.setItem('or_token', data.token);
            localStorage.setItem('or_user', JSON.stringify(data.user));
            updateAuthUI();
            closeAuthModal();
            loadWatchlist();
        } catch (err) {
            errEl.textContent = 'Network error — please try again';
            errEl.style.display = 'block';
        } finally {
            btn.disabled = false;
            btn.textContent = auth.mode === 'register' ? 'Create Account' : 'Sign In';
        }
    };

    window.signOut = function () {
        auth.token = null;
        auth.user = null;
        localStorage.removeItem('or_token');
        localStorage.removeItem('or_user');
        updateAuthUI();
    };

    function updateAuthUI() {
        if (auth.user) {
            $('loginBtn').style.display = 'none';
            $('registerBtn').style.display = 'none';
            $('userBadge').style.display = 'flex';
            $('userBadgeName').textContent = auth.user.name;
        } else {
            $('loginBtn').style.display = 'inline-flex';
            $('registerBtn').style.display = 'inline-flex';
            $('userBadge').style.display = 'none';
        }
        if (window.lucide) lucide.createIcons();
    }

    // Close modal on overlay click
    $('authModal').addEventListener('click', (e) => {
        if (e.target === $('authModal')) closeAuthModal();
    });

    // ── Filter Logic ─────────────────────────────────────────────────────────
    window.applyFilters = function () {
        const symbol = ($('filterSymbol').value || '').trim().toUpperCase();
        const signal = $('filterSignal').value;
        const priority = $('filterPriority').value;

        state.filteredAlerts = state.alerts.filter((a) => {
            if (symbol && !a.stock_symbol.includes(symbol)) return false;
            if (signal && a.signal_type !== signal) return false;
            if (priority && a.priority !== priority) return false;
            return true;
        });

        rebuildFeed();
    };

    window.clearFilters = function () {
        $('filterSymbol').value = '';
        $('filterSignal').value = '';
        $('filterPriority').value = '';
        state.filteredAlerts = [...state.alerts];
        rebuildFeed();
    };

    function rebuildFeed() {
        alertsFeed.innerHTML = '';
        const toShow = state.filteredAlerts.length ? state.filteredAlerts : state.alerts;
        if (toShow.length === 0) {
            emptyState.style.display = 'block';
            alertsFeed.appendChild(emptyState);
        } else {
            emptyState.style.display = 'none';
            toShow.forEach((a) => renderAlert(a, false));
        }
        alertCountBadge.textContent = `${toShow.length} alert${toShow.length !== 1 ? 's' : ''}`;
        if (window.lucide) lucide.createIcons();
    }

    // ── SSE Connection ───────────────────────────────────────────────────────
    function connectSSE() {
        const url = auth.token
            ? `/api/alerts/stream?token=${encodeURIComponent(auth.token)}`
            : '/api/alerts/stream';
        const es = new EventSource(url);

        es.addEventListener('alert', (e) => {
            try {
                const alert = JSON.parse(e.data);
                if (!state.seenAlertIds.has(alert.id)) {
                    state.seenAlertIds.add(alert.id);
                    state.alerts.unshift(alert);
                    state.filteredAlerts.unshift(alert);
                    renderAlert(alert, true);
                    updateSignalCounts(alert);
                }
            } catch (err) {
                console.error('Alert parse error:', err);
            }
        });

        es.addEventListener('status', (e) => {
            try {
                const status = JSON.parse(e.data);
                updatePipelineStatus(status);
                updateStats(status);
            } catch (err) {
                console.error('Status parse error:', err);
            }
        });

        es.onopen = () => {
            state.connected = true;
            $('liveDot').style.background = 'var(--accent-emerald)';
            $('liveText').textContent = 'Live';
            $('modeBadge').textContent = 'Live';
        };

        es.onerror = () => {
            state.connected = false;
            $('liveDot').style.background = 'var(--accent-rose)';
            $('liveText').textContent = 'Reconnecting...';
            $('modeBadge').textContent = 'Offline';
        };
    }

    // ── Initial Load ─────────────────────────────────────────────────────────
    async function loadInitialAlerts() {
        try {
            const res = await fetch('/api/alerts', { headers: authHeaders() });
            const data = await res.json();
            if (data.alerts && data.alerts.length > 0) {
                emptyState.style.display = 'none';
                data.alerts.forEach((alert) => {
                    if (!state.seenAlertIds.has(alert.id)) {
                        state.seenAlertIds.add(alert.id);
                        state.alerts.push(alert);
                        state.filteredAlerts.push(alert);
                        renderAlert(alert, false);
                        updateSignalCounts(alert);
                    }
                });
                if (window.lucide) lucide.createIcons();
            }
        } catch (err) {
            console.warn('Could not load initial alerts:', err);
        }
    }

    // ── Render Alert ─────────────────────────────────────────────────────────
    function renderAlert(alert, animate = true) {
        if (emptyState) emptyState.style.display = 'none';

        const signalClass = alert.signal_type.toLowerCase();
        const el = document.createElement('div');
        el.className = `alert-item ${signalClass}`;
        if (!animate) el.style.animation = 'none';
        el.id = `alert-${alert.id}`;

        const timeStr = formatTime(alert.created_at);
        const confidencePct = Math.round(alert.confidence_score * 100);
        const confColor = confidencePct >= 80 ? 'var(--accent-emerald)'
            : confidencePct >= 60 ? 'var(--accent-amber)' : 'var(--accent-rose)';

        let scoresHtml = '';
        if (alert.dimension_scores) {
            const ds = alert.dimension_scores;
            scoresHtml = `
                <div class="alert-scores">
                    ${scoreBox('MAG', ds.magnitude)}
                    ${scoreBox('CRED', ds.insider_credibility)}
                    ${scoreBox('TIME', ds.timing)}
                    ${scoreBox('SECT', ds.sector_momentum)}
                    ${scoreBox('HIST', ds.historical_match)}
                </div>
            `;
        }

        const tagsHtml = alert.tags.map((t) =>
            `<span class="alert-tag">${escapeHtml(t)}</span>`
        ).join('');

        const riskHtml = alert.risk_flags.map((f) =>
            `<div class="alert-risk-flag">
                <i data-lucide="alert-triangle" style="width:12px;height:12px;margin-right:4px;"></i>${escapeHtml(f)}
            </div>`
        ).join('');

        const reviewBadge = alert.needs_human_review
            ? `<div class="alert-review-badge">
                <i data-lucide="clock" style="width:12px;height:12px;margin-right:4px;"></i>
                Held for Human Review
               </div>`
            : '';

        el.innerHTML = `
            <div class="alert-header">
                <div>
                    <span class="alert-signal-badge ${signalClass}">${alert.signal_type}</span>
                    ${alert.filing_type ? `<span class="filing-type-badge">${alert.filing_type}</span>` : ''}
                </div>
                <div class="alert-meta">
                    <span class="alert-priority ${alert.priority}">${alert.priority}</span>
                    <span class="alert-time">${timeStr}</span>
                </div>
            </div>
            ${reviewBadge}
            <div class="alert-stock">
                <span class="alert-stock-symbol">${escapeHtml(alert.stock_symbol)}</span>
                <span class="alert-stock-name">${escapeHtml(alert.company_name)}</span>
            </div>
            <div class="alert-body">${escapeHtml(alert.body)}</div>
            ${scoresHtml}
            <div class="alert-base-rate">
                <i data-lucide="bar-chart" style="width:12px;height:12px;margin-right:4px;vertical-align:middle;"></i>
                ${escapeHtml(alert.historical_base_rate)}
            </div>
            ${riskHtml ? `<div class="alert-risk-flags">${riskHtml}</div>` : ''}
            <div class="alert-tags">${tagsHtml}</div>
            <div class="alert-confidence">
                <div class="confidence-bar-container">
                    <div class="confidence-bar" style="width: ${confidencePct}%; background: ${confColor};"></div>
                </div>
                <span class="confidence-label" style="color: ${confColor}">${confidencePct}%</span>
            </div>
            <div class="alert-actions">
                <button class="alert-action-btn" onclick="submitFeedback('${alert.id}', 'DISMISS')">
                    <i data-lucide="x" style="width:12px;height:12px;"></i> Dismiss
                </button>
                <button class="alert-action-btn watch-btn" onclick="submitFeedback('${alert.id}', 'WATCH')">
                    <i data-lucide="eye" style="width:12px;height:12px;"></i> Watch
                </button>
                <button class="alert-action-btn buy-btn" onclick="submitFeedback('${alert.id}', 'BUY_SIGNAL')">
                    <i data-lucide="bookmark" style="width:12px;height:12px;"></i> Signal
                </button>
            </div>
        `;

        alertsFeed.insertBefore(el, alertsFeed.firstChild);
        alertCountBadge.textContent = `${state.alerts.length} alert${state.alerts.length !== 1 ? 's' : ''}`;
    }

    function scoreBox(label, value) {
        const pct = Math.round(value * 100);
        const cls = pct >= 75 ? 'high' : pct >= 50 ? 'medium' : 'low';
        return `
            <div class="score-item">
                <div class="score-label">${label}</div>
                <div class="score-value ${cls}">${pct}%</div>
            </div>
        `;
    }

    // ── Pipeline Status ──────────────────────────────────────────────────────
    function updatePipelineStatus(status) {
        updateNodeStatus('status-watcher', status.filing_watcher);
        updateNodeStatus('status-classifier', status.signal_classifier);
        updateNodeStatus('status-enricher', status.context_enricher);
        updateNodeStatus('status-composer', status.alert_composer);
        updateNodeStatus('status-orchestrator', status.orchestrator);
    }

    function updateNodeStatus(elemId, statusText) {
        const el = $(elemId);
        if (!el) return;
        el.textContent = statusText;
        el.className = `pipeline-status ${statusText}`;
    }

    // ── Stats ────────────────────────────────────────────────────────────────
    function updateStats(status) {
        $('statFilings').textContent = status.total_filings_processed;
        $('statSignals').textContent = status.total_signals_generated;
        $('statAlerts').textContent = status.total_alerts_sent;
        $('totalFilings').textContent = status.total_filings_processed;
        $('totalSignals').textContent = status.total_signals_generated;
        $('totalAlerts').textContent = status.total_alerts_sent;
        $('uptime').textContent = formatUptime(status.uptime_seconds);
    }

    function updateSignalCounts(alert) {
        state.signalCounts[alert.signal_type]++;
        const total = Object.values(state.signalCounts).reduce((a, b) => a + b, 0);
        if (total > 0) {
            const bars = document.querySelectorAll('.signal-bar');
            const types = ['BULLISH', 'BEARISH', 'WATCH', 'NEUTRAL'];
            bars.forEach((bar, i) => {
                const ratio = state.signalCounts[types[i]] / total;
                bar.style.flex = Math.max(0.05, ratio);
            });
        }
        $('bullishCount').textContent = state.signalCounts.BULLISH;
        $('bearishCount').textContent = state.signalCounts.BEARISH;
        $('watchCount').textContent = state.signalCounts.WATCH;
        $('neutralCount').textContent = state.signalCounts.NEUTRAL;
    }

    // ── Watchlist ────────────────────────────────────────────────────────────
    async function loadWatchlist() {
        try {
            const res = await fetch('/api/users', { headers: authHeaders() });
            const data = await res.json();
            const user = auth.user
                ? data.users?.find((u) => u.id === auth.user.id) || data.users?.[0]
                : data.users?.[0];
            if (!user) return;

            const container = $('watchlistBody');
            const watchlistStocks = {
                RELIANCE: { name: 'Reliance Industries', price: 'Rs 2,480', change: '+1.44%', positive: true },
                INFY: { name: 'Infosys', price: 'Rs 1,685', change: '+0.88%', positive: true },
                HDFCBANK: { name: 'HDFC Bank', price: 'Rs 1,725', change: '+1.02%', positive: true },
                TATAMOTORS: { name: 'Tata Motors', price: 'Rs 628', change: '+1.65%', positive: true },
                ITC: { name: 'ITC', price: 'Rs 468', change: '+1.23%', positive: true },
                ADANIENT: { name: 'Adani Enterprises', price: 'Rs 2,650', change: '-2.23%', positive: false },
                BAJFINANCE: { name: 'Bajaj Finance', price: 'Rs 7,215', change: '+0.91%', positive: true },
                SWIGGY: { name: 'Swiggy', price: 'Rs 412', change: '+1.60%', positive: true },
                ZOMATO: { name: 'Zomato', price: 'Rs 182', change: '+2.35%', positive: true },
            };

            container.innerHTML = user.watchlist.map((sym) => {
                const stock = watchlistStocks[sym] || { name: sym, price: '—', change: '—', positive: true };
                return `
                    <div class="watchlist-item">
                        <div>
                            <div class="watchlist-symbol">${sym}</div>
                            <div class="watchlist-name">${stock.name}</div>
                        </div>
                        <div class="watchlist-price">
                            <div class="watchlist-price-value">${stock.price}</div>
                            <div class="watchlist-change ${stock.positive ? 'positive' : 'negative'}">${stock.change}</div>
                        </div>
                    </div>
                `;
            }).join('');
        } catch (err) {
            console.warn('Watchlist load error:', err);
        }
    }

    // ── Feedback ─────────────────────────────────────────────────────────────
    window.submitFeedback = async function (alertId, action) {
        try {
            const btn = event.target.closest('button');
            btn.style.opacity = '0.5';
            btn.style.pointerEvents = 'none';

            await fetch('/api/feedback', {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify({ alert_id: alertId, action, user_id: 'demo' }),
            });

            const alertEl = document.getElementById(`alert-${alertId}`);
            if (alertEl) {
                if (action === 'DISMISS') {
                    alertEl.style.opacity = '0.4';
                    alertEl.style.transform = 'scale(0.98)';
                } else if (action === 'WATCH') {
                    alertEl.style.borderColor = 'var(--accent-amber)';
                } else {
                    alertEl.style.borderColor = 'var(--accent-emerald)';
                }
            }

            loadFeedbackStats();
        } catch (err) {
            console.error('Feedback error:', err);
        }
    };

    async function loadFeedbackStats() {
        try {
            const res = await fetch('/api/feedback/stats');
            const stats = await res.json();

            const dismiss = stats.DISMISS || 0;
            const watch = stats.WATCH || 0;
            const buy = (stats.BUY_SIGNAL || 0) + (stats.SELL_SIGNAL || 0);
            const total = dismiss + watch + buy;

            $('fbDismiss').textContent = dismiss;
            $('fbWatch').textContent = watch;
            $('fbBuy').textContent = buy;

            if (total > 0) {
                $('feedbackBar').innerHTML = `
                    <div class="feedback-segment dismiss" style="flex: ${dismiss / total}">
                        ${dismiss > 0 ? `${Math.round(dismiss / total * 100)}%` : ''}
                    </div>
                    <div class="feedback-segment watch-fb" style="flex: ${watch / total}">
                        ${watch > 0 ? `${Math.round(watch / total * 100)}%` : ''}
                    </div>
                    <div class="feedback-segment buy" style="flex: ${buy / total}">
                        ${buy > 0 ? `${Math.round(buy / total * 100)}%` : ''}
                    </div>
                `;
            }
        } catch (_) {
            // Feedback stats are non-critical
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────
    function formatTime(ts) {
        try {
            return new Date(ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
        } catch {
            return '—';
        }
    }

    function formatUptime(seconds) {
        if (seconds < 60) return `${Math.round(seconds)}s`;
        if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
        return `${Math.round(seconds / 3600)}h`;
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── Initialize ────────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        updateAuthUI();
        loadInitialAlerts();
        loadWatchlist();
        loadFeedbackStats();
        connectSSE();
        state.filteredAlerts = [...state.alerts];

        setInterval(loadFeedbackStats, 15000);
    });
})();
