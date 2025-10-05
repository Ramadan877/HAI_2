// V2 single-session screen recorder (IIFE)
(function(){
    let mediaRecorder = null;
    let recordedChunks = [];
    let isRecording = false;
    let recordingStream = null;
    let recordingStartTime = null;
    let recordingTimer = null;
    let pendingRecordingBlob = null;
    let pendingMimeType = null;
    let hasPendingRecording = false;
    let pendingRecordingId = null; // id in IndexedDB when saved

    // --- IndexedDB helpers (simple wrapper) ---
    function openDB() {
        return new Promise((resolve, reject) => {
            try {
                const req = indexedDB.open('HAI_Recordings', 1);
                req.onupgradeneeded = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains('recordings')) {
                        db.createObjectStore('recordings', { keyPath: 'id', autoIncrement: true });
                    }
                };
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error || new Error('IndexedDB open error'));
            } catch (err) { reject(err); }
        });
    }

    function saveRecordingToDB(blob, meta) {
        return openDB().then(db => new Promise((resolve, reject) => {
            try {
                const tx = db.transaction('recordings', 'readwrite');
                const store = tx.objectStore('recordings');
                const rec = { blob: blob, meta: meta || {}, created: Date.now() };
                const req = store.add(rec);
                req.onsuccess = () => { const id = req.result; db.close(); resolve(id); };
                req.onerror = () => { db.close(); reject(req.error || new Error('add failed')); };
            } catch (err) { reject(err); }
        }));
    }

    function getAllRecordingsFromDB() {
        return openDB().then(db => new Promise((resolve, reject) => {
            try {
                const tx = db.transaction('recordings', 'readonly');
                const store = tx.objectStore('recordings');
                const req = store.getAll();
                req.onsuccess = () => { db.close(); resolve(req.result || []); };
                req.onerror = () => { db.close(); reject(req.error || new Error('getAll failed')); };
            } catch (err) { reject(err); }
        }));
    }

    function deleteRecordingFromDB(id) {
        return openDB().then(db => new Promise((resolve, reject) => {
            try {
                const tx = db.transaction('recordings', 'readwrite');
                const store = tx.objectStore('recordings');
                const req = store.delete(id);
                req.onsuccess = () => { db.close(); resolve(true); };
                req.onerror = () => { db.close(); reject(req.error || new Error('delete failed')); };
            } catch (err) { reject(err); }
        }));
    }

    // Upload queued DB recordings (retry loop)
    async function processPendingRecordings() {
        try {
            const rows = await getAllRecordingsFromDB();
            if (!rows || rows.length === 0) return;
            console.log('V2: processPendingRecordings found', rows.length, 'items');
            for (const r of rows) {
                try {
                    const form = new FormData();
                    const filename = r.meta && r.meta.filename ? r.meta.filename : `session_recording_${new Date(r.created).toISOString().replace(/[:.]/g,'')}.webm`;
                    form.append('screen_recording', r.blob, filename);
                    form.append('trial_type', r.meta && r.meta.trial_type ? r.meta.trial_type : (window.currentTrialType || 'unknown'));
                    form.append('participant_id', r.meta && r.meta.participant_id ? r.meta.participant_id : (window.participantId || 'unknown'));
                    const renderExportUrl = '/save_screen_recording';
                    const resp = await fetch(renderExportUrl, { method: 'POST', body: form });
                    if (resp && resp.ok) {
                        console.log('V2: uploaded pending recording id=', r.id);
                        await deleteRecordingFromDB(r.id);
                    } else {
                        console.warn('V2: server rejected pending recording id=', r.id, resp && resp.status);
                    }
                } catch (err) {
                    console.warn('V2: failed to upload pending recording id=', r.id, err);
                }
            }
        } catch (err) { console.error('V2: processPendingRecordings error', err); }
    }

    async function startScreenRecording() {
        try {
            console.log('V2: startScreenRecording called');
            recordedChunks = [];
            recordingStartTime = Date.now();

            recordingStream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    cursor: 'always',
                    width: { ideal: 1920, max: 3840 },
                    height: { ideal: 1080, max: 2160 },
                    frameRate: { ideal: 15, max: 30 }
                },
                audio: false
            });

            const candidates = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4'];
            let mimeType = '';
            for (const c of candidates) {
                if (c && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) { mimeType = c; break; }
            }

            const options = {};
            if (mimeType) options.mimeType = mimeType;

            mediaRecorder = new MediaRecorder(recordingStream, options);

            mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) recordedChunks.push(e.data);
            };

            mediaRecorder.onerror = (ev) => {
                console.error('V2 MediaRecorder error', ev.error);
            };

            mediaRecorder.onstop = () => {
                const elapsed = recordingStartTime ? Math.round((Date.now() - recordingStartTime) / 1000) : 0;
                console.log(`V2: mediaRecorder stopped after ${elapsed}s, chunks=${recordedChunks.length}`);

                if (recordedChunks.length === 0) {
                    console.warn('V2: no recording data available');
                    hasPendingRecording = false;
                    pendingRecordingBlob = null;
                    return;
                }

                try {
                    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'video/webm' });
                    pendingRecordingBlob = blob;
                    pendingMimeType = blob.type;
                    hasPendingRecording = true;
                    console.log('V2: pending recording assembled (no upload yet), bytes=', blob.size);
                    // Save to IndexedDB for reliable persistence and retry on next load
                    try {
                        const meta = { filename: `session_recording_${new Date().toISOString().replace(/[:.]/g,'')}.webm`, trial_type: window.currentTrialType || 'unknown', participant_id: window.participantId || 'unknown' };
                        saveRecordingToDB(blob, meta).then(id => {
                            pendingRecordingId = id;
                            console.log('V2: saved pending recording to DB id=', id);
                            // Attempt immediate upload now that we have the assembled blob and a DB record.
                            uploadPendingRecording().then(result => {
                                console.log('V2: immediate upload result after save:', result);
                                if (result && (result.success || result.beacon || (result.status && result.status === 'success'))) {
                                    // If upload succeeded, remove DB entry
                                    if (pendingRecordingId) {
                                        deleteRecordingFromDB(pendingRecordingId).then(() => { pendingRecordingId = null; console.log('V2: deleted DB record after successful upload'); }).catch(() => {});
                                    }
                                }
                            }).catch((upErr) => { console.warn('V2: immediate upload failed, will retry later', upErr); });
                        }).catch(dbErr => { console.warn('V2: failed to save pending recording to DB', dbErr); });
                    } catch (dbEx) { console.warn('V2: IndexedDB save error', dbEx); }
                } catch (err) {
                    console.error('V2: failed to assemble pending blob', err);
                    pendingRecordingBlob = null;
                    hasPendingRecording = false;
                }

            };

            mediaRecorder.start();
            isRecording = true;
            console.log('V2: recording started');

            recordingTimer = setInterval(() => {
                if (!isRecording) return;
                const elapsed = Math.round((Date.now() - recordingStartTime)/1000);
                const mm = Math.floor(elapsed/60); const ss = elapsed%60;
                console.log(`V2: recording ${mm}:${ss.toString().padStart(2,'0')}`);
            }, 30000);

            recordingStream.getVideoTracks()[0].onended = async () => {
                console.log('V2: display media track ended by user — attempting to re-acquire');
                try {
                    for (let attempt = 0; attempt < 3; attempt++) {
                        try {
                            const newStream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: 'always' }, audio: false });
                            const newTrack = newStream.getVideoTracks()[0];
                            if (newTrack) {
                                try { recordingStream.getTracks().forEach(t => t.stop()); } catch (_) {}
                                recordingStream = newStream;
                                try {
                                    if (mediaRecorder && mediaRecorder.stream && typeof mediaRecorder.stream.removeTrack === 'function' && typeof mediaRecorder.stream.addTrack === 'function') {
                                        mediaRecorder.stream.getVideoTracks().forEach(t => { try { mediaRecorder.stream.removeTrack(t); } catch(_){} });
                                        mediaRecorder.stream.addTrack(newTrack);
                                    }
                                } catch (remErr) { console.warn('V2: could not replace track on mediaRecorder.stream', remErr); }
                                // wire the onended handler again
                                newTrack.onended = recordingStream.getVideoTracks()[0].onended;
                                console.log('V2: re-acquired display track, continuing recording');
                                return;
                            }
                        } catch (e) {
                            console.warn('V2 reacquire attempt failed', e);
                        }
                        await new Promise(r => setTimeout(r, 1000));
                    }
                    console.warn('V2: failed to re-acquire display track after attempts; leaving pending recording (no upload)');
                } catch (e) {
                    console.error('V2 re-acquire handler error', e);
                }
            };

            return Promise.resolve();
        } catch (err) {
            console.error('V2: startScreenRecording error', err);
            throw err;
        }
    }

    async function stopScreenRecording() {
        if (!mediaRecorder) return;
        if (mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    }

    async function uploadSessionRecording(blob) {
        console.log('V2: uploadSessionRecording: preparing form');
        const form = new FormData();
        const filename = `session_recording_${new Date().toISOString().replace(/[:.]/g,'')}.webm`;
        form.append('screen_recording', blob, filename);
        form.append('trial_type', window.currentTrialType || 'unknown');
        form.append('participant_id', window.participantId || 'unknown');

    const renderExportUrl = '/save_screen_recording';

        console.log('V2: uploading to', renderExportUrl, 'filename', filename);

        try {
            const resp = await fetch(renderExportUrl, { method: 'POST', body: form, keepalive: true });
            if (!resp.ok) {
                const text = await resp.text().catch(()=>'<no-body>');
                throw new Error(`V2 upload failed: ${resp.status} ${text}`);
            }
            const j = await resp.json().catch(()=>null);
            console.log('V2: upload completed', j);
            return j;
        } catch (err) {
            console.error('V2: uploadSessionRecording error', err);
            throw err;
        }
    }

    async function uploadPendingRecording() {
        if (!hasPendingRecording || !pendingRecordingBlob) {
            console.log('V2: no pending recording to upload');
            return null;
        }

        console.log('V2: uploadPendingRecording: attempting upload, bytes=', pendingRecordingBlob.size);
    const renderExportUrl = '/save_screen_recording';

        try {
            const form = new FormData();
            const filename = `session_recording_${new Date().toISOString().replace(/[:.]/g,'')}.webm`;
            form.append('screen_recording', pendingRecordingBlob, filename);
            form.append('trial_type', window.currentTrialType || 'unknown');
            form.append('participant_id', window.participantId || 'unknown');

            const resp = await fetch(renderExportUrl, { method: 'POST', body: form, keepalive: true });
            if (!resp.ok) {
                const t = await resp.text().catch(()=>'<no-body>');
                throw new Error(`V2 upload failed ${resp.status} ${t}`);
            }
            const j = await resp.json().catch(()=>null);
            console.log('V2: uploadPendingRecording completed', j);
            hasPendingRecording = false;
            pendingRecordingBlob = null;
            pendingMimeType = null;
            // If we saved this recording to IndexedDB earlier, remove its record now
            try {
                if (pendingRecordingId) {
                    await deleteRecordingFromDB(pendingRecordingId);
                    console.log('V2: deleted pending recording from DB id=', pendingRecordingId);
                    pendingRecordingId = null;
                }
            } catch (delErr) { console.warn('V2: failed to delete DB record after upload', delErr); }

            return j;
        } catch (err) {
            console.error('V2: uploadPendingRecording failed, will attempt sendBeacon as fallback', err);
            try {
                if (navigator && navigator.sendBeacon) {
                    const beaconOk = navigator.sendBeacon(renderExportUrl, pendingRecordingBlob);
                    console.log('V2: sendBeacon fallback result', beaconOk);
                    if (beaconOk) {
                        hasPendingRecording = false;
                        pendingRecordingBlob = null;
                        pendingMimeType = null;
                        return { beacon: true };
                    }
                }
            } catch (be) {
                console.error('V2: sendBeacon fallback error', be);
            }

            throw err;
        }
    }

    async function cleanupScreenRecording() {
        console.log('V2 Starting cleanup...');
        try {
            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                console.log('V2 cleanup: stopping mediaRecorder to assemble pending blob');
                const stopPromise = new Promise((resolve) => {
                    const originalOnStop = mediaRecorder.onstop;
                    mediaRecorder.onstop = () => {
                        try { if (originalOnStop) originalOnStop(); } catch(_){}
                        resolve();
                    };
                });
                mediaRecorder.stop();
                await stopPromise;
            }

            if (hasPendingRecording && pendingRecordingBlob) {
                try {
                    await uploadPendingRecording();
                } catch (e) {
                    console.error('V2 cleanup upload failed (left pending for retry):', e);
                }
            }

            if (recordingStream) {
                recordingStream.getTracks().forEach(track => track.stop());
                recordingStream = null;
            } else if (mediaRecorder && mediaRecorder.stream) {
                try { mediaRecorder.stream.getTracks().forEach(track => track.stop()); } catch(_){}
            }

            if (recordingTimer) { clearInterval(recordingTimer); recordingTimer = null; }
            mediaRecorder = null;
            isRecording = false;
            recordingStartTime = null;
            recordedChunks = [];
            // Attempt to process any DB-persisted pending recordings (retry uploads)
            try { processPendingRecordings().catch(()=>{}); } catch(_){}
        } catch (error) {
            console.error('V2 Error during cleanup:', error);
        }
    }

    window.startScreenRecording = startScreenRecording;
    window.stopScreenRecording = stopScreenRecording;

    window.addEventListener('beforeunload', (event) => {
        console.log('V2 beforeunload event triggered');
        try {
            // Try to stop recorder (will assemble pending onstop asynchronously)
            try { if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop(); } catch(_){}

            // If we already have a pending blob, try a synchronous sendBeacon to improve chance of server receipt.
            if (hasPendingRecording && pendingRecordingBlob && navigator && navigator.sendBeacon) {
                try { sendBeaconForPending(); } catch (e) { console.warn('V2 beforeunload sendBeacon failed', e); }
            } else if (recordedChunks && recordedChunks.length > 0 && navigator && navigator.sendBeacon) {
                try { sendBeaconForPending(); } catch (e) { console.warn('V2 beforeunload sendBeacon failed (assembled)', e); }
            }
            // Allow unload to proceed; async fetches are not reliable here.
        } catch (e) {
            console.warn('V2 beforeunload handler error', e);
        }
    });

    document.addEventListener('visibilitychange', async () => {
        console.log('V2 visibilitychange event triggered:', document.visibilityState);
    });

    window.addEventListener('unload', () => {
        console.log('V2 unload event triggered');
        try {
            try { if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop(); } catch(_){}
            if (hasPendingRecording && pendingRecordingBlob && navigator && navigator.sendBeacon) {
                try { sendBeaconForPending(); } catch(e){ console.warn('V2 unload sendBeacon failed', e); }
            } else if (recordedChunks && recordedChunks.length > 0 && navigator && navigator.sendBeacon) {
                try { sendBeaconForPending(); } catch(e){ console.warn('V2 unload sendBeacon failed (assembled)', e); }
            }
        } catch (e) { console.warn('V2 unload handler error', e); }
    });

    window.addEventListener('close', async () => {
        console.log('V2 close event triggered');
        if (isRecording) {
            try { await cleanupScreenRecording(); } catch(e){ console.warn('V2 close cleanup failed', e); }
        }
    });

    window.uploadPendingRecording = uploadPendingRecording;

    // Synchronous sendBeacon fallback that sends the pending blob (or assembles from recordedChunks) as FormData.
    function sendBeaconForPending() {
        if (!navigator || !navigator.sendBeacon) { console.warn('V2: sendBeacon not available'); return false; }
        try {
            const blobToSend = (hasPendingRecording && pendingRecordingBlob) ? pendingRecordingBlob : (recordedChunks && recordedChunks.length ? new Blob(recordedChunks, { type: pendingMimeType || 'video/webm' }) : null);
            if (!blobToSend) { console.warn('V2: nothing to send via beacon'); return false; }
            const filename = `session_recording_${new Date().toISOString().replace(/[:.]/g,'')}.webm`;
            const participant = encodeURIComponent(window.participantId || 'unknown');
            const trial = encodeURIComponent(window.currentTrialType || 'unknown');
            const url = `/save_screen_recording?participant_id=${participant}&trial_type=${trial}&filename=${encodeURIComponent(filename)}`;
            // send raw blob as beacon body (server will treat raw body as file)
            const ok = navigator.sendBeacon(url, blobToSend);
            console.log('V2: sendBeacon result', ok, 'url', url, 'bytes', blobToSend.size);
            if (ok) {
                hasPendingRecording = false; pendingRecordingBlob = null; pendingMimeType = null;
            }
            return ok;
        } catch (err) {
            console.error('V2: sendBeaconForPending error', err);
            return false;
        }
    }

})();

// On load, attempt to process any pending recordings saved to IndexedDB
try { if (typeof window !== 'undefined') { window.addEventListener('load', () => { try { processPendingRecordings().catch(()=>{}); } catch(_){} }); } } catch(_){}