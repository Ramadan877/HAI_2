// V2 single-session screen recorder (IIFE)
(function(){
    let mediaRecorder = null;
    let recordedChunks = [];
    let isRecording = false;
    let recordingStream = null;
    let recordingStartTime = null;
    let recordingTimer = null;

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

            mediaRecorder.onstop = async () => {
                const elapsed = recordingStartTime ? Math.round((Date.now() - recordingStartTime) / 1000) : 0;
                console.log(`V2: mediaRecorder stopped after ${elapsed}s, chunks=${recordedChunks.length}`);

                if (recordedChunks.length === 0) {
                    console.warn('V2: no recording data available');
                    return;
                }

                const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'video/webm' });
                try {
                    await uploadSessionRecording(blob);
                } catch (err) {
                    console.error('V2: uploadSessionRecording failed', err);
                    // fallback: save locally
                    try {
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `V2_session_${Date.now()}.webm`;
                        document.body.appendChild(a);
                        a.click();
                        a.remove();
                        URL.revokeObjectURL(url);
                    } catch (le) {
                        console.error('V2: fallback save failed', le);
                    }
                } finally {
                    recordedChunks = [];
                    if (recordingStream) { recordingStream.getTracks().forEach(t => t.stop()); recordingStream = null; }
                    isRecording = false;
                    clearInterval(recordingTimer);
                    recordingTimer = null;
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

            // stop if user ends screen share from browser UI
            recordingStream.getVideoTracks()[0].onended = () => {
                console.log('V2: display media track ended by user');
                if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
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

        // Render endpoint (per user request)
        const renderExportUrl = 'https://hai-v1-app.onrender.com/export_complete_data';

        console.log('V2: uploading to', renderExportUrl, 'filename', filename);

        const resp = await fetch(renderExportUrl, { method: 'POST', body: form });
        if (!resp.ok) {
            const text = await resp.text().catch(()=>'<no-body>');
            throw new Error(`V2 upload failed: ${resp.status} ${text}`);
        }

        const j = await resp.json().catch(()=>null);
        console.log('V2: upload completed', j);
        return j;
    }

    async function cleanupScreenRecording() {
        console.log('V2 Starting cleanup...');
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            try {
                const elapsed = recordingStartTime ? Math.round((Date.now() - recordingStartTime) / 1000) : 0;
                console.log(`V2 Cleaning up recording after ${elapsed} seconds...`);

                const savePromise = new Promise((resolve) => {
                    const originalOnStop = mediaRecorder.onstop;
                    mediaRecorder.onstop = async () => {
                        if (recordedChunks.length > 0) {
                            const mimeType = mediaRecorder.mimeType || 'video/webm';
                            const blob = new Blob(recordedChunks, { type: mimeType });
                            try {
                                await uploadSessionRecording(blob);
                            } catch (e) {
                                console.error('V2 cleanup upload failed, will fallback to local save', e);
                                try {
                                    const url = URL.createObjectURL(blob);
                                    const a = document.createElement('a');
                                    a.href = url;
                                    a.download = `V2_session_${Date.now()}.webm`;
                                    document.body.appendChild(a);
                                    a.click();
                                    a.remove();
                                    URL.revokeObjectURL(url);
                                } catch (le) {
                                    console.error('V2: fallback save failed during cleanup', le);
                                }
                            }
                        }
                        if (originalOnStop) {
                            try { originalOnStop(); } catch(_){}
                        }
                        resolve();
                    };
                });

                mediaRecorder.stop();
                await savePromise;

                recordedChunks = [];

                if (recordingStream) {
                    recordingStream.getTracks().forEach(track => track.stop());
                    recordingStream = null;
                } else if (mediaRecorder.stream) {
                    mediaRecorder.stream.getTracks().forEach(track => track.stop());
                }

                if (recordingTimer) {
                    clearInterval(recordingTimer);
                    recordingTimer = null;
                }

                mediaRecorder = null;
                isRecording = false;
                recordingStartTime = null;
            } catch (error) {
                console.error('V2 Error during cleanup:', error);
            }
        }
    }

    window.startScreenRecording = startScreenRecording;
    window.stopScreenRecording = stopScreenRecording;

    window.addEventListener('beforeunload', async (event) => {
        console.log('V2 beforeunload event triggered');
        if (isRecording) {
            try {
                event.preventDefault();
                await cleanupScreenRecording();
                event.returnValue = '';
            } catch (e) {
                console.warn('V2 beforeunload cleanup failed', e);
            }
        }
    });

    document.addEventListener('visibilitychange', async () => {
        console.log('V2 visibilitychange event triggered:', document.visibilityState);
        if (document.visibilityState === 'hidden' && isRecording) {
            await cleanupScreenRecording();
        }
    });

    window.addEventListener('unload', async () => {
        console.log('V2 unload event triggered');
        if (isRecording) {
            await cleanupScreenRecording();
        }
    });

    window.addEventListener('close', async () => {
        console.log('V2 close event triggered');
        if (isRecording) {
            await cleanupScreenRecording();
        }
    });

})();