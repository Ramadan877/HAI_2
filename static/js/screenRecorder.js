let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;
let recordingStream = null;
let recordingStartTime = null;
let recordingTimer = null;

function checkRecordingStatus() {
    if (mediaRecorder) {
        console.log(`V2 Recording Status: ${mediaRecorder.state}, Chunks: ${recordedChunks.length}, IsRecording: ${isRecording}`);
        if (recordingStartTime) {
            const elapsed = Math.round((Date.now() - recordingStartTime) / 1000);
            const minutes = Math.floor(elapsed / 60);
            const seconds = elapsed % 60;
            console.log(`V2 Time elapsed: ${minutes}:${seconds.toString().padStart(2, '0')}`);
        }
    } else {
        console.log('V2 No media recorder active');
    }
}

window.checkRecordingStatusV2 = checkRecordingStatus;

async function startScreenRecording() {
    try {
        console.log('Starting V2 screen recording for long session...');
        recordingStartTime = Date.now();
        
        recordingStream = await navigator.mediaDevices.getDisplayMedia({
            video: {
                cursor: "always",
                width: { ideal: 1920, max: 1920 },
                height: { ideal: 1080, max: 1080 },
                frameRate: { ideal: 15, max: 30 }
            },
            audio: false
        });
        
        console.log('Got display media stream for V2');
        
        let mimeType = 'video/webm;codecs=vp8'; 
        if (!MediaRecorder.isTypeSupported(mimeType)) {
            mimeType = 'video/webm';
            if (!MediaRecorder.isTypeSupported(mimeType)) {
                mimeType = 'video/mp4';
                if (!MediaRecorder.isTypeSupported(mimeType)) {
                    mimeType = ''; 
                }
            }
        }
        
        console.log('V2 using MIME type for recording:', mimeType || 'browser default');
        
        const options = {
            videoBitsPerSecond: 500000,
        };
        
        if (mimeType) {
            options.mimeType = mimeType;
        }
        
        mediaRecorder = new MediaRecorder(recordingStream, options);
        recordedChunks = [];
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                recordedChunks.push(event.data);
                console.log(`V2 Recording chunk ${recordedChunks.length}: ${event.data.size} bytes`);
                
                if (recordedChunks.length % 5 === 0) {
                    const totalSize = recordedChunks.reduce((sum, chunk) => sum + chunk.size, 0);
                    const elapsed = Math.round((Date.now() - recordingStartTime) / 1000);
                    console.log(`V2 Recording progress: ${recordedChunks.length} chunks, ${Math.round(totalSize/1024/1024)}MB, ${elapsed}s`);
                }
            } else {
                console.log('V2 Received empty data chunk - this is normal for continuous recording');
            }
        };
        
        mediaRecorder.onstop = async () => {
            const elapsed = Math.round((Date.now() - recordingStartTime) / 1000);
            console.log(`V2 Recording stopped after ${elapsed} seconds, chunks collected: ${recordedChunks.length}`);
            
            if (recordedChunks.length > 0) {
                const blob = new Blob(recordedChunks, { type: mimeType.split(';')[0] });
                const sizeMB = Math.round(blob.size / 1024 / 1024);
                console.log(`V2 Final recording: ${sizeMB}MB, duration: ${elapsed}s`);
                await saveScreenRecording(blob);
            } else {
                console.error('V2: No recording chunks available');
            }
            recordedChunks = [];
            
            if (recordingStream) {
                recordingStream.getTracks().forEach(track => track.stop());
                recordingStream = null;
            }
            
            if (recordingTimer) {
                clearInterval(recordingTimer);
                recordingTimer = null;
            }
        };
        
        mediaRecorder.onerror = (event) => {
            console.error('V2 MediaRecorder error:', event.error);
            isRecording = false;
            if (recordingTimer) {
                clearInterval(recordingTimer);
                recordingTimer = null;
            }
        };
        

    mediaRecorder.start(10000); // 10 seconds timeslice for robust long recordings
    isRecording = true;
    console.log('V2 Long-duration recording started successfully (continuous mode, 10s chunks)');
        
    recordingTimer = setInterval(() => {
            if (isRecording && mediaRecorder && mediaRecorder.state === 'recording') {
                const elapsed = Math.round((Date.now() - recordingStartTime) / 1000);
                const minutes = Math.floor(elapsed / 60);
                const seconds = elapsed % 60;
                console.log(`V2 Recording active: ${minutes}:${seconds.toString().padStart(2, '0')}`);
            } else {
                console.warn('V2 Recording stopped unexpectedly, state:', mediaRecorder?.state);
                if (recordingTimer) {
                    clearInterval(recordingTimer);
                    recordingTimer = null;
                }
            }
        }, 30000); 
        
        recordingStream.getVideoTracks()[0].onended = () => {
            console.log('V2 Screen sharing ended by user');
            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                mediaRecorder.stop();
                isRecording = false;
            }
        };
        
    } catch (error) {
        console.error('V2 Error starting screen recording:', error);
        isRecording = false;
        recordingStartTime = null;
        if (recordingTimer) {
            clearInterval(recordingTimer);
            recordingTimer = null;
        }
    }
}

async function stopScreenRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        const elapsed = recordingStartTime ? Math.round((Date.now() - recordingStartTime) / 1000) : 0;
        console.log(`V2 Manually stopping recording after ${elapsed} seconds...`);
        mediaRecorder.stop();
        isRecording = false;
    }
    
    if (recordingTimer) {
        clearInterval(recordingTimer);
        recordingTimer = null;
    }
    
    if (recordingStream) {
        recordingStream.getTracks().forEach(track => track.stop());
        recordingStream = null;
    }
    
    recordingStartTime = null;
}

async function saveScreenRecording(blob) {
    try {
        const sizeMB = Math.round(blob.size / 1024 / 1024);
        console.log(`V2 Preparing to save ${sizeMB}MB recording...`);
        
        const formData = new FormData();
        
        const timestamp = new Date().toISOString().replace(/[:.]/g, '').slice(0, 15);
        const filename = `screen_recording_${timestamp}.webm`;
        
        formData.append('screen_recording', blob, filename);
        
        const currentTrialType = window.currentTrialType;
        const participantId = window.participantId;
        
        console.log('V2 Current trial type:', currentTrialType);
        console.log('V2 Current participant ID:', participantId);
        
        if (!currentTrialType || !participantId) {
            console.error('V2 Missing trial type or participant ID:', { currentTrialType, participantId });
            return;
        }
        
        const trialFolderMap = {
            'Trial_1': 'main_task_1',
            'Trial_2': 'main_task_2',
            'Test': 'test_task'
        };
        
        const trialFolderName = trialFolderMap[currentTrialType] || currentTrialType.toLowerCase();
        
        formData.append('trial_type', trialFolderName);
        formData.append('participant_id', participantId);
        
        console.log('V2 Saving screen recording for:', {
            participant_id: participantId,
            trial_type: trialFolderName,
            filename: filename,
            blob_size_mb: sizeMB
        });
        
        if (sizeMB > 10) {
            console.log('V2 Large file detected, this may take a while...');
        }
        
        const response = await fetch('/save_screen_recording', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
        }
        
        const result = await response.json();
        console.log(`V2 Screen recording saved successfully: ${result.message || 'OK'}`);
        
        if (result.size_mb) {
            console.log(`V2 Server confirmed size: ${result.size_mb}MB`);
        }
        
    } catch (error) {
        console.error('V2 Error saving screen recording:', error);
        
        if (blob.size > 0) {
            console.log('V2 Attempting to save recording locally as fallback...');
            try {
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `V2_screen_recording_backup_${Date.now()}.webm`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                console.log('V2 Recording saved locally as backup');
            } catch (localError) {
                console.error('V2 Failed to save locally:', localError);
            }
        }
    }
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
                        await saveScreenRecording(blob);
                    }
                    if (originalOnStop) {
                        originalOnStop();
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

window.addEventListener('beforeunload', async (event) => {
    console.log('V2 beforeunload event triggered');
    if (isRecording) {
        event.preventDefault(); 
        await cleanupScreenRecording();
        event.returnValue = '';
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