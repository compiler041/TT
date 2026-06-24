const API_URL = 'http://localhost:8000/api';

const PERIOD_LABELS = [
    "9-10", "10-11", "11-12", "12-1",
    "1-2 (LUNCH)", "2-3", "3-4", "4-5", "5-6"
];

const DAYS = ["MON", "TUE", "WED", "THU", "FRI"];

let currentTimetable = null;

document.addEventListener('DOMContentLoaded', () => {
    setupDragAndDrop();
    setupSolveButton();
    setupTabs();
    setupSelects();
    setupExportButton();
});

function setupExportButton() {
    const btn = document.getElementById('btn-export');
    if (btn) {
        btn.addEventListener('click', () => {
            window.location.href = `${API_URL}/export`;
        });
    }
}

function setupTabs() {
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            // Remove active from all tabs
            tabs.forEach(t => t.classList.remove('active'));
            // Add active to clicked
            tab.classList.add('active');
            
            // Hide all tab content
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            // Show target
            document.getElementById(tab.dataset.target).classList.add('active');
            
            // Render specific grid based on tab
            const target = tab.dataset.target;
            if (target === 'tab-division') {
                const val = document.getElementById('select-division').value;
                if (val) renderGrid('division-grid', currentTimetable.divisions[val].grid);
            } else if (target === 'tab-faculty') {
                const val = document.getElementById('select-faculty').value;
                if (val) fetchAndRenderFaculty(val);
            } else if (target === 'tab-room') {
                const val = document.getElementById('select-room').value;
                if (val) fetchAndRenderRoom(val);
            }
        });
    });
}

function setupSelects() {
    document.getElementById('select-division').addEventListener('change', (e) => {
        if (e.target.value) renderGrid('division-grid', currentTimetable.divisions[e.target.value].grid);
    });
    
    document.getElementById('select-faculty').addEventListener('change', (e) => {
        if (e.target.value) fetchAndRenderFaculty(e.target.value);
    });
    
    document.getElementById('select-room').addEventListener('change', (e) => {
        if (e.target.value) fetchAndRenderRoom(e.target.value);
    });
}

async function fetchAndRenderFaculty(facId) {
    try {
        const res = await fetch(`${API_URL}/timetable/faculty/${facId}`);
        const json = await res.json();
        renderGrid('faculty-grid', json.grid, true);
    } catch (e) {
        console.error(e);
    }
}

async function fetchAndRenderRoom(roomNo) {
    try {
        const res = await fetch(`${API_URL}/timetable/room/${roomNo}`);
        const json = await res.json();
        renderGrid('room-grid', json.grid, true);
    } catch (e) {
        console.error(e);
    }
}

function setupDragAndDrop() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) {
            handleFileUpload(e.target.files[0]);
        }
    });
}

async function handleFileUpload(file) {
    if (!file.name.endsWith('.xlsx')) {
        alert('Please upload a valid .xlsx file');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    document.getElementById('upload-progress').classList.remove('hidden');
    document.getElementById('drop-zone').classList.add('hidden');

    try {
        const res = await fetch(`${API_URL}/upload`, {
            method: 'POST',
            body: formData
        });
        const json = await res.json();
        
        document.getElementById('upload-progress').classList.add('hidden');

        if (json.success) {
            document.getElementById('upload-stats').classList.remove('hidden');
            document.getElementById('stat-divs').textContent = json.data.divisions;
            document.getElementById('stat-courses').textContent = json.data.courses;
            document.getElementById('stat-faculty').textContent = json.data.faculty;
            document.getElementById('stat-rooms').textContent = json.data.rooms;
            
            document.getElementById('btn-solve').classList.remove('hidden');
        } else {
            alert('Error parsing file: ' + json.error);
            document.getElementById('drop-zone').classList.remove('hidden');
        }
    } catch (e) {
        alert('Failed to connect to backend.');
        document.getElementById('upload-progress').classList.add('hidden');
        document.getElementById('drop-zone').classList.remove('hidden');
    }
}

function setupSolveButton() {
    const btn = document.getElementById('btn-solve');
    let timerInterval;

    btn.addEventListener('click', async () => {
        btn.classList.add('hidden');
        document.getElementById('solve-progress').classList.remove('hidden');
        
        let seconds = 0;
        const timerEl = document.getElementById('solve-timer');
        timerInterval = setInterval(() => {
            seconds++;
            timerEl.textContent = seconds;
        }, 1000);

        try {
            const res = await fetch(`${API_URL}/solve`, { method: 'POST' });
            const json = await res.json();
            
            clearInterval(timerInterval);
            document.getElementById('solve-progress').classList.add('hidden');

            document.getElementById('upload-view').classList.add('hidden');
            document.getElementById('results-view').classList.remove('hidden');
            
            const badge = document.getElementById('solve-result-badge');
            badge.textContent = json.status;
            if (json.status !== "OPTIMAL" && json.status !== "FEASIBLE") {
                badge.classList.add('error');
            }
            document.getElementById('solve-time-display').textContent = `Time: ${(json.solve_time_ms / 1000).toFixed(1)}s`;
            
            await fetchConflicts();
            await fetchTimetable();
            
        } catch (e) {
            clearInterval(timerInterval);
            alert('Failed to solve.');
            btn.classList.remove('hidden');
            document.getElementById('solve-progress').classList.add('hidden');
        }
    });
}

async function fetchConflicts() {
    try {
        const res = await fetch(`${API_URL}/conflicts`);
        const json = await res.json();
        
        if (json.conflicts && Object.keys(json.conflicts).length > 0) {
            const panel = document.getElementById('conflicts-panel');
            const content = document.getElementById('conflicts-content');
            panel.classList.remove('hidden');
            content.innerHTML = '';
            
            for (const [category, messages] of Object.entries(json.conflicts)) {
                let html = `<div class="conflict-category"><strong>${category}</strong><ul>`;
                messages.forEach(msg => {
                    html += `<li>${msg}</li>`;
                });
                html += `</ul></div>`;
                content.innerHTML += html;
            }
        }
    } catch(e) {
        console.error(e);
    }
}

async function fetchTimetable() {
    try {
        const res = await fetch(`${API_URL}/timetable`);
        const json = await res.json();
        currentTimetable = json;

        // Populate Division Select
        const selectDiv = document.getElementById('select-division');
        selectDiv.innerHTML = '';
        json.division_list.forEach(div => {
            const option = document.createElement('option');
            option.value = div;
            option.textContent = div;
            selectDiv.appendChild(option);
        });

        // Populate Faculty Select
        const selectFac = document.getElementById('select-faculty');
        selectFac.innerHTML = '';
        json.faculty_list.forEach(fac => {
            const option = document.createElement('option');
            option.value = fac;
            option.textContent = fac;
            selectFac.appendChild(option);
        });
        
        // Populate Room Select
        const selectRoom = document.getElementById('select-room');
        selectRoom.innerHTML = '';
        json.room_list.forEach(room => {
            const option = document.createElement('option');
            option.value = room;
            option.textContent = room;
            selectRoom.appendChild(option);
        });

        if (json.division_list.length > 0) {
            renderGrid('division-grid', json.divisions[json.division_list[0]].grid);
        }
    } catch (e) {
        console.error(e);
        alert('Failed to fetch timetable data.');
    }
}

function renderGrid(containerId, gridData, isSimpleView = false) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';

    // Headers
    const timeHeader = createCell('grid-header time-label', 'TIME');
    timeHeader.style.gridColumn = 1;
    timeHeader.style.gridRow = 1;
    container.appendChild(timeHeader);

    DAYS.forEach((day, index) => {
        const header = createCell('grid-header', day);
        header.style.gridColumn = index + 2;
        header.style.gridRow = 1;
        container.appendChild(header);
    });

    // Group records by slot_id
    const slotMap = {};
    gridData.forEach(rec => {
        if (!slotMap[rec.slot_id]) slotMap[rec.slot_id] = [];
        slotMap[rec.slot_id].push(rec);
    });

    const occupied = new Set();

    for (let p = 1; p <= 9; p++) {
        const timeCell = createCell('grid-cell time-label', PERIOD_LABELS[p-1]);
        timeCell.style.gridColumn = 1;
        timeCell.style.gridRow = p + 1;
        container.appendChild(timeCell);

        if (p === 5) {
            // Lunch Row
            for (let d = 0; d < 5; d++) {
                const lunchCell = createCell('grid-cell session-lunch', 'LUNCH');
                lunchCell.style.gridColumn = d + 2;
                lunchCell.style.gridRow = p + 1;
                container.appendChild(lunchCell);
                occupied.add(`${d}-${p}`);
            }
            continue;
        }

        for (let d = 0; d < 5; d++) {
            if (occupied.has(`${d}-${p}`)) continue;

            const slotId = d * 9 + (p - 1);
            const sessions = slotMap[slotId] || [];

            if (sessions.length > 0) {
                const cell = document.createElement('div');
                cell.className = 'grid-cell';
                cell.style.gridColumn = d + 2;
                cell.style.gridRow = p + 1;

                let duration = 1;
                if (sessions[0].subject_type === 'THEORY') {
                    cell.className += ' session-theory';
                    cell.innerHTML = `
                        <div class="course-code">${sessions[0].course_code}</div>
                        <div class="course-meta">${sessions[0].faculty.join(', ')} • ${sessions[0].rooms.join(', ')}</div>
                    `;
                } else if (sessions[0].subject_type === 'PRACTICAL') {
                    cell.className += ' session-practical';
                    duration = sessions[0].duration; // Get duration from session
                    cell.style.gridRowEnd = `span ${duration}`;
                    
                    if (isSimpleView) {
                        cell.innerHTML = `
                            <div class="course-code">${sessions[0].course_code} (P)</div>
                            <div class="course-meta">${sessions[0].faculty.join(', ')} • ${sessions[0].rooms.join(', ')}</div>
                        `;
                    } else {
                        // Sort sessions by batch
                        const sortedSessions = [...sessions].sort((a, b) => (a.batches[0] || '').localeCompare(b.batches[0] || ''));
                        let html = '<div class="practical-group-container">';
                        sortedSessions.forEach(s => {
                            html += `
                                <div class="practical-sub-session">
                                    <span class="batch-badge">${s.batches.join(',')}</span>
                                    <b style="margin-bottom: 2px;">${s.course_code}</b>
                                    <span class="course-meta">${s.rooms[0] || '-'}</span>
                                    <span class="course-meta">${s.faculty[0] || '-'}</span>
                                </div>
                            `;
                        });
                        html += '</div>';
                        cell.innerHTML = html;
                    }
                } else {
                    cell.className += ' session-tutorial';
                    if (isSimpleView) {
                        cell.innerHTML = `
                            <div class="course-code">${sessions[0].course_code} (T)</div>
                            <div class="course-meta">${sessions[0].faculty.join(', ')} • ${sessions[0].rooms.join(', ')}</div>
                        `;
                    } else {
                        const sortedSessions = [...sessions].sort((a, b) => (a.batches[0] || '').localeCompare(b.batches[0] || ''));
                        let html = '<div class="practical-group-container">';
                        sortedSessions.forEach(s => {
                            html += `
                                <div class="practical-sub-session">
                                    <span class="batch-badge">${s.batches.join(',')}</span>
                                    <b style="margin-bottom: 2px;">${s.course_code} (T)</b>
                                    <span class="course-meta">${s.rooms[0] || '-'}</span>
                                    <span class="course-meta">${s.faculty[0] || '-'}</span>
                                </div>
                            `;
                        });
                        html += '</div>';
                        cell.innerHTML = html;
                    }
                }
                
                container.appendChild(cell);
                
                for (let i = 0; i < duration; i++) {
                    occupied.add(`${d}-${p + i}`);
                }
            } else {
                const cell = document.createElement('div');
                cell.className = 'grid-cell';
                cell.style.gridColumn = d + 2;
                cell.style.gridRow = p + 1;
                container.appendChild(cell);
            }
        }
    }
}

function createCell(className, content) {
    const div = document.createElement('div');
    div.className = className;
    div.innerHTML = content;
    return div;
}
