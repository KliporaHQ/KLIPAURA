const POLL_MS = 2500;
let statusPollInterval = null;
let currentVideoUrl = null;

// Dynamic API base for both local and production (relative URLs)
const API_BASE = '';

function updateUI(state) {
    // Update status
    document.getElementById('status-stage').textContent = state.stage || 'idle';
    document.getElementById('status-progress').textContent = `${state.progress || 0}%`;
    document.getElementById('status-state').textContent = state.status || 'idle';

    // UI reflects system mode - surgical fix
    const modeEl = document.getElementById('system-mode');
    if (modeEl) {
        modeEl.textContent = state.mode || 'DIRECT';
    }

    // Progress bar
    const progressFill = document.getElementById('progress-fill');
    progressFill.style.width = `${state.progress || 0}%`;
    document.getElementById('progress-text').textContent = `${state.progress || 0}%`;

    // Current topic
    if (state.current_topic) {
        document.getElementById('current-topic').textContent = state.current_topic;
    }

    // Logs — update text only, no full innerHTML rebuild
    const logsContainer = document.getElementById('logs-container');
    const logs = Array.isArray(state.logs) ? [...state.logs] : [];
    if (state.error) {
        const errLine = `ERROR: ${state.error}`;
        if (!logs.some((l) => String(l).includes(String(state.error)))) {
            logs.push(errLine);
        }
    }
    if (logs.length > 0) {
        logsContainer.textContent = logs.join('\n');
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }

    // Update stages
    const stages = ['script', 'media', 'render', 'publish'];
    stages.forEach((stage, index) => {
        const stageEl = document.getElementById(`stage-${stage}`);
        const dotEl = document.getElementById(`dot-${stage}`);

        if (!stageEl || !dotEl) return;

        stageEl.classList.remove('active', 'done');
        dotEl.className = 'status-dot idle';

        if (state.stage === stage || (state.progress > (index * 25) && state.progress < ((index + 1) * 25))) {
            stageEl.classList.add('active');
            dotEl.className = 'status-dot running';
        } else if (state.progress >= ((index + 1) * 25) || state.stage === 'completed') {
            stageEl.classList.add('done');
            dotEl.className = 'status-dot done';
        }
    });

    // Video — only set src when URL actually changes (avoid reload every poll)
    if (state.output && state.output.includes('.mp4')) {
        const videoEl = document.getElementById('video-player');
        const videoInfo = document.getElementById('video-info');
        const newVideoUrl = `${API_BASE}/pipeline/output`;
        const resolvedNew = new URL(newVideoUrl, window.location.href).href;
        if (newVideoUrl && videoEl.src !== resolvedNew) {
            videoEl.src = newVideoUrl;
        }
        currentVideoUrl = state.output;
        videoInfo.textContent = 'Video ready - click to play';
        videoInfo.style.color = '#4ade80';
    }
}

async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/pipeline/status`);
        if (res.ok) {
            const state = await res.json();
            updateUI(state);
        }
    } catch (e) {
        console.error('Status fetch failed', e);
    }
}

async function runPipeline() {
    const topicInput = document.getElementById('topic-input');
    const topic = topicInput.value.trim();
    const productEl = document.getElementById('product-url');
    const productUrl = productEl ? productEl.value.trim() : '';
    const aspectRatio = document.getElementById('aspect-ratio')
        ? document.getElementById('aspect-ratio').value
        : '9:16';
    const durationTier = document.getElementById('duration-tier')
        ? document.getElementById('duration-tier').value
        : 'SHORT';

    if (!topic && !productUrl) {
        alert('Enter a topic and/or a product URL');
        return;
    }

    const runBtn = document.getElementById('run-btn');
    runBtn.disabled = true;
    runBtn.textContent = 'RUNNING...';

    const body = {
        topic: topic || null,
        product_url: productUrl || null,
        avatar_id: 'default',
        video_config: {
            aspect_ratio: aspectRatio,
            duration_tier: durationTier
        }
    };

    try {
        const res = await fetch(`${API_BASE}/pipeline/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        if (res.ok) {
            const result = await res.json();
            console.log('Pipeline started:', result);

            startPollingOnce();
            setTimeout(() => {
                fetchStatus();
            }, 500);
        } else {
            alert('Failed to start pipeline');
        }
    } catch (err) {
        console.error(err);
        alert('Connection error to API');
    } finally {
        setTimeout(() => {
            runBtn.disabled = false;
            runBtn.textContent = 'RUN PIPELINE';
        }, 2000);
    }
}

function startPollingOnce() {
    if (!window.pollingStarted) {
        window.pollingStarted = true;
        statusPollInterval = setInterval(fetchStatus, POLL_MS);
    }
}

function resetPipeline() {
    if (statusPollInterval) {
        clearInterval(statusPollInterval);
        statusPollInterval = null;
    }
    window.pollingStarted = false;
    document.getElementById('logs-container').textContent = '[SYSTEM] Pipeline reset';
    const videoEl = document.getElementById('video-player');
    videoEl.removeAttribute('src');
    videoEl.load();
    currentVideoUrl = null;
    fetchStatus();
    location.reload();
}

// Keyboard support
document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && document.getElementById('topic-input') === document.activeElement) {
        runPipeline();
    }
});

// Init
window.onload = function () {
    console.log('%cKLIP-AVATAR Core V1 Dashboard initialized', 'color: #60a5fa; font-weight: bold');

    fetchStatus();

    startPollingOnce();

    fetch(`${API_BASE}/`)
        .then((r) => r.json())
        .then(console.log)
        .catch(() => {});
};
