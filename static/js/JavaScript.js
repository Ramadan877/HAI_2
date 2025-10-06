document.addEventListener("DOMContentLoaded", function () {
    const avatar = document.getElementById("chat-avatar");
    const siriOrb = document.querySelector(".siri-orb");
    const blobs = document.querySelectorAll(".blob");
    const startSound = document.getElementById("start-sound");
    const stopSound = document.getElementById("stop-sound");
    const introAudio = document.getElementById("intro-audio");
    const conceptAudio = document.getElementById("concept-audio");
    const aiAudio = document.getElementById("ai-response-audio");
    const conceptIndicator = document.getElementById("current-concept");
    const pdfCanvas = document.getElementById("pdf-canvas");
    const currentPageNum = document.getElementById("current-page-num");
    const totalPages = document.getElementById("total-pages");
    const prevPageBtn = document.getElementById("prev-page");
    const nextPageBtn = document.getElementById("next-page");
    const loadingSpinner = document.getElementById("loading-spinner");
    const startOverlay = document.getElementById("start-overlay");
    
    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;
    let currentPage = 1;
    let introPlayed = false;
    let avatarClickState = 0; // 0: Initial, 1: Intro played, 2: Recording
    let lastConcept = "";
    let isAnimating = false;
    let waves = [];
    let waveCount = 0;
    let pdfDoc = null;
    let pageRendering = false;
    let pageNumPending = null;
    let scale = 1.5;
    let isWaitingForAI = false;
    let meteorAnimationId = null;
    let meteor = null;
    let meteorTrail = null;
    let conceptIntrosPlayed = {};
    let isAudioPlaying = false;
    let appStarted = false;
    let currentTrialType = ""; 

    loadPDF();
    
    window.startWithTrial = function(trialType) {
        if (appStarted) return;
        
        currentTrialType = trialType;
        console.log(`Starting with trial type: ${trialType}`);
        
        if (typeof changeTrial === 'function') {
            changeTrial(trialType);
        }
        
        appStarted = true;
        
        startOverlay.style.opacity = "0";
        setTimeout(() => {
            startOverlay.style.display = "none";
        }, 500);
        
        playIntroAudio();
        animateBlobs();
    }

    function checkIfAudioPlaying() {
        return !introAudio.paused || !conceptAudio.paused || !aiAudio.paused;
    }
    
    function setAudioLock(isLocked) {
        isAudioPlaying = isLocked;
        
        if (isLocked) {
            avatar.style.opacity = "0.7";
            avatar.style.cursor = "wait";
        } else {
            avatar.style.opacity = "1";
            avatar.style.cursor = "pointer";
        }
    }
    
    function setupAudioLockEvents(audioElement) {
        audioElement.addEventListener('play', () => {
            setAudioLock(true);
        });
        
        audioElement.addEventListener('ended', () => {
            setTimeout(() => {
                if (!checkIfAudioPlaying()) {
                    setAudioLock(false);
                }
            }, 100);
        });
        
        audioElement.addEventListener('pause', () => {
            setTimeout(() => {
                if (!checkIfAudioPlaying()) {
                    setAudioLock(false);
                }
            }, 100);
        });
        
        audioElement.addEventListener('error', () => {
            setAudioLock(false);
        });
    }
    
    setupAudioLockEvents(introAudio);
    setupAudioLockEvents(conceptAudio);
    setupAudioLockEvents(aiAudio);
     
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
     
    function loadPDF() {
        loadingSpinner.style.display = 'block';
        const pdfUrl = '/resources/Univariate Analysis.pdf';
        
        pdfjsLib.getDocument(pdfUrl).promise.then(function(pdfDoc_) {
            pdfDoc = pdfDoc_;
            totalPages.textContent = pdfDoc.numPages;
            loadingSpinner.style.display = 'none';
            
            renderPage(currentPage);
        }).catch(function(error) {
            console.error('Error loading PDF:', error);
            loadingSpinner.style.display = 'none';
        });
    }
     
    function renderPage(num) {
        pageRendering = true;
        loadingSpinner.style.display = 'block';
        
        pdfDoc.getPage(num).then(function(page) {
            const viewport = page.getViewport({ scale: scale });
            pdfCanvas.height = viewport.height;
            pdfCanvas.width = viewport.width;
            
            const renderContext = {
                canvasContext: pdfCanvas.getContext('2d'),
                viewport: viewport
            };
            
            const renderTask = page.render(renderContext);
            
            renderTask.promise.then(function() {
                pageRendering = false;
                loadingSpinner.style.display = 'none';
                
                if (pageNumPending !== null) {
                    renderPage(pageNumPending);
                    pageNumPending = null;
                }
            });
        });
        
        currentPageNum.textContent = num;
        currentPage = num;
        updateConceptIndicator();
    }
     
    function onPrevPage() {
        if (currentPage <= 1) return;
        queueRenderPage(currentPage - 1);
    }
     
    function onNextPage() {
        if (currentPage >= pdfDoc.numPages) return;
        queueRenderPage(currentPage + 1);
    }
     
    function queueRenderPage(num) {
        if (pageRendering) {
            pageNumPending = num;
        } else {
            renderPage(num);
        }
    }
     
    prevPageBtn.addEventListener('click', onPrevPage);
    nextPageBtn.addEventListener('click', onNextPage);
     
    document.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowRight') {
            onNextPage();
        } else if (e.key === 'ArrowLeft') {
            onPrevPage();
        }
    });
     
    // Siri Animation Functions
    function animateBlobs() {
        blobs.forEach((blob, index) => {
            const speed = 2 + index * 0.5;
            const time = performance.now() / 1000;
            const x = Math.sin(time * speed) * 10;
            const y = Math.cos(time * (speed + 0.5)) * 10;
            
            blob.style.transform = `translate(${x}px, ${y}px) scale(${0.8 + Math.sin(time * speed) * 0.1})`;
        });
        
        requestAnimationFrame(animateBlobs);
    }
     
    function createWave() {
        const wave = document.createElement('div');
        wave.className = 'wave';
        wave.id = `wave-${waveCount++}`;
        avatar.appendChild(wave);
        waves.push(wave);
        
        setTimeout(() => {
            wave.style.transition = 'all 2s cubic-bezier(0.1, 0.8, 0.1, 1)';
            wave.style.transform = 'scale(1.5)';
            wave.style.opacity = '0';
        }, 10);
        
        setTimeout(() => {
            avatar.removeChild(wave);
            waves = waves.filter(w => w !== wave);
        }, 2000);
    }
     
    function activateSiriOrb() {
        if (isAnimating) return;
        isAnimating = true;
        
        createWave();
        setTimeout(createWave, 200);
        setTimeout(createWave, 400);
        
        siriOrb.style.transform = 'scale(1.1)';
        setTimeout(() => {
            siriOrb.style.transform = 'scale(1)';
        }, 300);
        
        blobs.forEach((blob, index) => {
            const delay = index * 100;
            setTimeout(() => {
                blob.style.transform = 'scale(1.2) translate(0, 0)';
                setTimeout(() => {
                    blob.style.transform = 'scale(1) translate(0, 0)';
                }, 400);
            }, delay);
        });
        
        setTimeout(() => {
            isAnimating = false;
        }, 800);
    }
     
    function createMeteorElements() {
        removeMeteorElements();
        
        meteor = document.createElement('div');
        meteor.className = 'meteor';
        avatar.appendChild(meteor);
        
        meteorTrail = document.createElement('div');
        meteorTrail.className = 'meteor-trail';
        avatar.appendChild(meteorTrail);
        
        meteor.style.opacity = '0';
        meteorTrail.style.opacity = '0';
        
        meteor.style.animation = 'orbit 2s linear infinite, fadeIn 0.5s forwards';
        
        meteor.style.left = '50%';
        meteor.style.top = '50%';
        meteorTrail.style.left = '50%';
        meteorTrail.style.top = '50%';
    }
     
    function removeMeteorElements() {
        const existingMeteor = avatar.querySelector('.meteor');
        const existingTrail = avatar.querySelector('.meteor-trail');
        
        if (existingMeteor) {
            avatar.removeChild(existingMeteor);
        }
        
        if (existingTrail) {
            avatar.removeChild(existingTrail);
        }
    }
     
    function animateMeteor() {
        if (!meteor || !meteorTrail) return;
        
        const time = performance.now() / 1000;
        const orbitRadius = 75; 
        const speed = 2;
        
        const angle = time * speed;
        const x = Math.cos(angle) * orbitRadius;
        const y = Math.sin(angle) * orbitRadius;
        
        meteor.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
        
        const trailAngle = angle - Math.PI / 2;
        const trailX = Math.cos(trailAngle) * 10;
        const trailY = Math.sin(trailAngle) * 10;
        
        meteorTrail.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) rotate(${angle + Math.PI/2}rad)`;
        meteorTrail.style.opacity = '0.8';
        
        meteorAnimationId = requestAnimationFrame(animateMeteor);
    }
     
    function startMeteorOrbit() {
        createMeteorElements();
        meteorAnimationId = requestAnimationFrame(animateMeteor);
        isWaitingForAI = true;
    }
     
    function stopMeteorOrbit() {
        if (meteorAnimationId) {
            cancelAnimationFrame(meteorAnimationId);
            meteorAnimationId = null;
        }
        
        if (meteor) {
            meteor.style.animation = 'none';
            meteor.style.opacity = '0';
            
            setTimeout(() => {
                removeMeteorElements();
            }, 500);
        }
        
        isWaitingForAI = false;
    }
     
    const conceptMapping = {
        1: "Univariate Analysis",
        2: "Univariate Analysis",
        3: "Measures of Central Tendency",
        4: "Measures of Dispersion"
    };
     
    function getConceptForPage(page) {
        let closestPage = Object.keys(conceptMapping)
            .map(Number)
            .filter(p => p <= page)
            .sort((a, b) => b - a)[0] || 2;
            
        return conceptMapping[closestPage] || "Unknown Concept";
    }
     
    function updateConceptIndicator() {
        const currentConcept = getConceptForPage(currentPage);
        conceptIndicator.textContent = `Current Concept: ${currentConcept} (Page ${currentPage})`;
        
        if (lastConcept !== currentConcept) {
            lastConcept = currentConcept;
            avatarClickState = 0;
        }
    }
     
    function playIntroAudio() {
        if (introPlayed) return;
        
        fetch("/get_intro_audio")
            .then(response => response.json())
            .then(data => {
                if (data.intro_audio_url) {
                    introAudio.src = data.intro_audio_url;
                    introAudio.play()
                        .then(() => {
                            introPlayed = true;
                            activateSiriOrb(); 
                        })
                        .catch(error => {
                            console.log("Autoplay blocked, waiting for user interaction");
                            document.addEventListener("click", function playOnClick() {
                                introAudio.play();
                                introPlayed = true;
                                activateSiriOrb(); 
                                document.removeEventListener("click", playOnClick);
                            }, { once: true });
                        });
                }
            })
            .catch(error => {
               console.error("Error loading intro audio:", error);
               setAudioLock(false);
       });            
   }
     
     
   // Handle avatar click - implements the 3-state click functionality
   avatar.addEventListener("click", function() {
       if (!appStarted) return;
       
       if (isAudioPlaying) {
           console.log("Avatar click blocked - audio is currently playing");
           return;
       }

       let concept = getConceptForPage(currentPage);
       
       activateSiriOrb();
       
       switch(avatarClickState) {
           case 0: // First click: Play concept intro (only if not played before)
               if (!conceptIntrosPlayed[concept]) {
                   playConceptIntroAudio(concept);
                   conceptIntrosPlayed[concept] = true;
               } else {
                   console.log(`Intro for concept "${concept}" already played, skipping to recording state`);
               }
               avatarClickState = 1;
               break;
               
           case 1: // Second click: Start recording
               startRecording();
               avatarClickState = 2;
               break;
               
           case 2: // Third click: Stop recording
               stopRecording();
               avatarClickState = 1; 
               break;
       }
   });
   
   function playConceptIntroAudio(concept) {
       if (isRecording) {
           stopRecording();
       }
       
       conceptAudio.src = `/get_concept_audio/${encodeURIComponent(concept)}`;
       
       conceptAudio.onloadedmetadata = function() {
           conceptAudio.play()
               .then(() => {
                   console.log(`Playing intro for concept: ${concept}`);
                   siriOrb.style.boxShadow = "0 0 20px 5px rgba(0, 0, 255, 0.7)";
               })
               .catch(error => {
                   console.error("Error playing concept intro:", error);
                   setAudioLock(false);
               });                
       };
       
       conceptAudio.onerror = function() {
           console.error("Error loading concept audio");
           avatarClickState = 1; 
           setAudioLock(false);
       };
       
       conceptAudio.onended = function() {
           siriOrb.style.boxShadow = "none";
       };
   }
     
   async function startRecording() {
       try {
           isRecording = true;
           audioChunks = [];

           await startSound.play();
                              
           let stream = await navigator.mediaDevices.getUserMedia({ audio: true });
           mediaRecorder = new MediaRecorder(stream);
           
           mediaRecorder.ondataavailable = event => {
               if (event.data.size > 0) {
                   audioChunks.push(event.data);
               }
           };
           
           mediaRecorder.start();
           
           siriOrb.style.boxShadow = "0 0 25px 5px rgba(0, 0, 0, 0.7)";
           
           blobs.forEach(blob => {
               blob.style.animation = "pulse 1s infinite alternate";
           });
           
           const style = document.createElement('style');
           style.id = 'pulse-animation';
           style.textContent = `
               @keyframes pulse {
                   0% { transform: scale(0.8); }
                   100% { transform: scale(1.2); }
               }
           `;
           document.head.appendChild(style);
           
           console.log("Recording started...");
       } catch (error) {
           console.error("Error starting recording:", error);
           isRecording = false;
           avatarClickState = 0; 
           setAudioLock(false);
       }
   }
   
   function stopRecording() {
       if (!isRecording || !mediaRecorder) return;
       
       isRecording = false;
       mediaRecorder.stop();
       
       siriOrb.style.boxShadow = "none";
       
       blobs.forEach(blob => {
           blob.style.animation = "none";
       });
       
       const pulseStyle = document.getElementById('pulse-animation');
       if (pulseStyle) {
           pulseStyle.remove();
       }
       
       stopSound.play();
       
       startMeteorOrbit();
       
       mediaRecorder.onstop = async () => {
           try {
               const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
               
               const formData = new FormData();
               formData.append("audio", audioBlob, "user_audio.wav");
               
               const currentConcept = getConceptForPage(currentPage);
               formData.append("concept_name", currentConcept);
               
               console.log(`Sending explanation for concept: ${currentConcept}`);
               
               const response = await fetch("/submit_message", {
                   method: "POST",
                   body: formData
               });
               
               const data = await response.json();
               
               if (data.ai_audio_url) {
                   stopMeteorOrbit();

                   try {
                       const text = data.response || '';
                       fetch('/synthesize', {
                           method: 'POST',
                           headers: { 'Content-Type': 'application/json' },
                           body: JSON.stringify({ text: text, format: 'mp3' })
                       }).then(res => {
                           if (!res.ok) throw new Error('TTS request failed');
                           return res.arrayBuffer();
                       }).then(arrayBuffer => {
                           const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                           return audioCtx.decodeAudioData(arrayBuffer).then(audioBuffer => ({ audioCtx, audioBuffer }));
                       }).then(({ audioCtx, audioBuffer }) => {
                           const src = audioCtx.createBufferSource();
                           src.buffer = audioBuffer;
                           src.connect(audioCtx.destination);
                           setAudioLock(true);
                           src.onended = () => {
                               siriOrb.style.boxShadow = 'none';
                               setAudioLock(false);
                           };
                           activateSiriOrb();
                           siriOrb.style.boxShadow = "0 0 20px 5px rgba(0, 128, 255, 0.7)";
                           src.start(0);
                       }).catch(err => {
                           console.error('TTS/playback error, falling back to server file:', err);
                           aiAudio.src = data.ai_audio_url;
                           aiAudio.onended = () => { siriOrb.style.boxShadow = 'none'; setAudioLock(false); };
                           aiAudio.onerror = (e) => { console.error('Fallback play error', e); setAudioLock(false); };
                           aiAudio.play().catch(e => { console.error('Fallback play failed', e); setAudioLock(false); });
                       });
                   } catch (err) {
                       console.error('Error invoking synthesize:', err);
                       // Ensure UI unlock if synthesis process failed
                       setAudioLock(false);
                       stopMeteorOrbit();
                   }
               } else {
                   console.error("AI audio URL missing from response");
                   stopMeteorOrbit();
               }
            } catch (error) {
                console.error("Error processing recording:", error);
                stopMeteorOrbit();
            }
            
            mediaRecorder.stream.getTracks().forEach(track => track.stop());
        };
    }

    startSound.addEventListener('play', () => setAudioLock(true));
    startSound.addEventListener('ended', () => setAudioLock(false));
    stopSound.addEventListener('play', () => setAudioLock(true));
    stopSound.addEventListener('ended', () => setAudioLock(false));
    
    loadPDF();
});