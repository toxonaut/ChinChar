document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const flashcard = document.getElementById('flashcard');
    const characterHanzi = document.getElementById('character-hanzi');
    const characterHanziBack = document.getElementById('character-hanzi-back');
    const characterPinyin = document.getElementById('character-pinyin');
    const characterMeaning = document.getElementById('character-meaning');
    const btnDontKnow = document.getElementById('btn-dont-know');
    const btnUnsure = document.getElementById('btn-unsure');
    const btnKnow = document.getElementById('btn-know');
    const btnDemote = document.getElementById('btn-demote');
    const btnAiDescription = document.getElementById('btn-ai-description');

    const aiModal = document.getElementById('ai-modal');
    const aiModalTitle = document.getElementById('ai-modal-title');
    const aiModalBody = document.getElementById('ai-modal-body');
    const aiModalClose = document.getElementById('ai-modal-close');
    
    // Stats elements
    const knowCount = document.getElementById('know-count');
    const unsureCount = document.getElementById('unsure-count');
    const dontKnowCount = document.getElementById('dont-know-count');
    const progressPercent = document.getElementById('progress-percent');
    
    // Current character data
    let currentCharacter = null;
    
    // Initialize the app
    init();
    
    // Event listeners
    flashcard.addEventListener('click', () => {
        flashcard.classList.toggle('flipped');
    });
    
    btnDontKnow.addEventListener('click', (e) => handleAnswer(0, e));
    btnUnsure.addEventListener('click', (e) => handleAnswer(1, e));
    btnKnow.addEventListener('click', (e) => handleAnswer(2, e));

    if (btnDemote) {
        btnDemote.addEventListener('click', (e) => handleDemote(e));
    }

    if (btnAiDescription) {
        btnAiDescription.addEventListener('click', (e) => handleAiDescription(e));
    }

    if (aiModalClose) {
        aiModalClose.addEventListener('click', () => hideAiModal());
    }

    if (aiModal) {
        aiModal.addEventListener('click', (e) => {
            if (e.target === aiModal) {
                hideAiModal();
            }
        });
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideAiModal();
        }
    });
    
    // Functions
    async function init() {
        await loadStats();
        const params = new URLSearchParams(window.location.search);
        const charId = params.get('char_id');
        if (charId) {
            await loadSpecificCharacter(charId);
            // Clean up the URL so refresh loads a random character
            window.history.replaceState({}, '', '/');
        } else {
            await loadNextCharacter();
        }
    }

    async function loadSpecificCharacter(charId) {
        try {
            const response = await fetch(`/api/character/${charId}`);
            const data = await response.json();
            if (!response.ok || (data && data.error)) {
                console.error('Error loading specific character:', data.error);
                await loadNextCharacter();
                return;
            }
            currentCharacter = data;
            characterHanzi.textContent = data.hanzi;
            flashcard.classList.remove('flipped');
            await loadCharacterDetails(data.id);
        } catch (error) {
            console.error('Error loading specific character:', error);
            await loadNextCharacter();
        }
    }
    
    async function loadNextCharacter() {
        try {
            const response = await fetch('/api/character/next');
            console.log(`Next character response status: ${response.status}`);
            
            // Get the response text for debugging
            const responseText = await response.text();
            console.log('Next character response text:', responseText);
            
            // Parse the JSON (if possible)
            let data;
            try {
                data = JSON.parse(responseText);
                console.log('Parsed next character data:', data);
            } catch (jsonError) {
                console.error('Error parsing next character JSON response:', jsonError);
                throw new Error('Failed to parse server response');
            }
            
            if (!response.ok || (data && data.error)) {
                if (response.status === 404 || (data && data.error === 'No characters available')) {
                    // No characters available - show a friendly message
                    characterHanzi.textContent = '?';
                    alert('No characters available. Please make sure characters.txt is properly loaded.');
                    return;
                }
                throw new Error((data && data.error) || 'Failed to load next character');
            }
            
            currentCharacter = data;
            
            // Update the front of the card
            characterHanzi.textContent = data.hanzi;
            
            // Reset the card to front side
            flashcard.classList.remove('flipped');
            
            // Load character details for the back of the card
            await loadCharacterDetails(data.id);
            
        } catch (error) {
            console.error('Error loading next character:', error);
            characterHanzi.textContent = '?';
            alert('Error loading character. Please try again.');
        }
    }
    
    async function loadCharacterDetails(characterId) {
        try {
            const response = await fetch(`/api/character/${characterId}`);
            console.log(`Character details response status: ${response.status}`);
            
            // Get the response text for debugging
            const responseText = await response.text();
            console.log('Character details response text:', responseText);
            
            // Parse the JSON (if possible)
            let data;
            try {
                data = JSON.parse(responseText);
                console.log('Parsed character details data:', data);
            } catch (jsonError) {
                console.error('Error parsing character details JSON response:', jsonError);
                throw new Error('Failed to parse server response');
            }
            
            if (!response.ok || (data && data.error)) {
                throw new Error((data && data.error) || 'Failed to load character details');
            }
            
            // Update the back of the card
            characterHanziBack.textContent = data.hanzi;
            characterPinyin.textContent = convertPinyinToToneMarks(data.pinyin);
            characterMeaning.textContent = data.meaning;
            
        } catch (error) {
            console.error('Error loading character details:', error);
        }
    }

    function showAiModal(title, body) {
        if (!aiModal || !aiModalTitle || !aiModalBody) return;
        aiModalTitle.textContent = title;
        aiModalBody.textContent = body;
        aiModal.style.display = 'flex';
    }

    function hideAiModal() {
        if (!aiModal) return;
        aiModal.style.display = 'none';
    }

    async function handleAiDescription(event) {
        event.preventDefault();
        event.stopPropagation();

        if (!currentCharacter) {
            console.error('No current character for AI description');
            return;
        }

        if (!btnAiDescription) return;

        btnAiDescription.disabled = true;
        showAiModal(`AI description: ${currentCharacter.hanzi}`, 'Loading...');

        try {
            const response = await fetch(`/api/character/${currentCharacter.id}/ai-description`);
            const responseText = await response.text();

            let data;
            try {
                data = responseText ? JSON.parse(responseText) : null;
            } catch (jsonError) {
                console.error('Error parsing AI description JSON response:', jsonError);
                throw new Error('Failed to parse server response');
            }

            if (!response.ok || (data && data.error)) {
                throw new Error((data && data.error) || 'Failed to load AI description');
            }

            const cachedSuffix = data && data.cached ? ' (cached)' : '';
            showAiModal(`AI description: ${currentCharacter.hanzi}${cachedSuffix}`, (data && data.content) || '');
        } catch (error) {
            console.error('Error getting AI description:', error);
            showAiModal(`AI description: ${currentCharacter.hanzi}`, error.message || 'Error loading AI description. Please try again.');
        } finally {
            btnAiDescription.disabled = false;
        }
    }

    async function handleDemote(event) {
        event.preventDefault();
        event.stopPropagation();

        if (!currentCharacter) {
            console.error('No current character to demote');
            return;
        }

        if (!btnDemote) return;

        btnDemote.disabled = true;

        try {
            const response = await fetch('/api/character/demote', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ character_id: currentCharacter.id })
            });

            const responseText = await response.text();
            let data;
            try {
                data = responseText ? JSON.parse(responseText) : null;
            } catch (jsonError) {
                console.error('Error parsing demote JSON response:', jsonError);
                throw new Error('Failed to parse server response');
            }

            if (!response.ok || (data && data.error)) {
                throw new Error((data && data.error) || 'Failed to demote character');
            }

            flashcard.classList.remove('flipped');
            await loadNextCharacter();
        } catch (error) {
            console.error('Error demoting character:', error);
            alert('Error updating character frequency. Please try again.');
        } finally {
            btnDemote.disabled = false;
        }
    }
    
    async function handleAnswer(familiarity, event) {
        event.preventDefault(); // Prevent any default behavior
        
        if (!currentCharacter) {
            console.error('No current character to submit answer for');
            return;
        }
        
        console.log(`Submitting answer: character_id=${currentCharacter.id}, familiarity=${familiarity}`);
        
        // Create the request data
        const requestData = {
            character_id: currentCharacter.id,
            familiarity: familiarity
        };
        
        console.log('Request data:', requestData);
        
        // Get the button and its navigation URL
        const button = event.currentTarget;
        const navigateUrl = button.getAttribute('data-url');
        
        try {
            // Send the request to update progress
            const response = await fetch('/api/progress', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestData)
            });
            
            console.log(`Response status: ${response.status}`);
            
            // Get the response text for debugging
            const responseText = await response.text();
            console.log('Response text:', responseText);
            
            // Parse the JSON (if possible)
            let data;
            try {
                if (responseText) {
                    data = JSON.parse(responseText);
                    console.log('Parsed response data:', data);
                }
            } catch (jsonError) {
                console.error('Error parsing JSON response:', jsonError);
                throw new Error('Failed to parse server response');
            }
            
            // Check if the response was successful
            if (!response.ok || (data && data.error)) {
                throw new Error((data && data.error) || 'Failed to submit answer');
            }
            
            console.log('Successfully submitted answer');
            
            // Handle navigation if Ctrl/Cmd key was pressed
            if (event.ctrlKey || event.metaKey) {
                console.log(`Navigating to: ${navigateUrl}`);
                window.location.href = navigateUrl;
                return;
            }
            
            // Otherwise, continue with flashcards
            // Flip card back to front side
            flashcard.classList.remove('flipped');
            
            // Small delay before loading the next character for better UX
            setTimeout(async () => {
                try {
                    // Load the next character
                    await loadStats();
                    await loadNextCharacter();
                } catch (loadError) {
                    console.error('Error loading next character:', loadError);
                }
            }, 300);
            
        } catch (error) {
            console.error('Error submitting answer:', error);
            alert('Error submitting answer. Please try again.');
        }
    }
    
    async function loadStats() {
        try {
            const response = await fetch('/api/stats');
            console.log(`Stats response status: ${response.status}`);
            
            // Get the response text for debugging
            const responseText = await response.text();
            console.log('Stats response text:', responseText);
            
            // Parse the JSON (if possible)
            let data;
            try {
                data = JSON.parse(responseText);
                console.log('Parsed stats data:', data);
            } catch (jsonError) {
                console.error('Error parsing stats JSON response:', jsonError);
                throw new Error('Failed to parse server response');
            }
            
            if (!response.ok || (data && data.error)) {
                throw new Error((data && data.error) || 'Failed to load stats');
            }
            
            // Update stats display
            knowCount.textContent = data.know_count;
            unsureCount.textContent = data.unsure_count;
            dontKnowCount.textContent = data.dont_know_count;
            
            // Calculate and display progress percentage
            const totalReviewed = data.reviewed_characters;
            const totalCharacters = data.total_characters;
            
            if (totalCharacters > 0) {
                const percent = Math.round((totalReviewed / totalCharacters) * 100);
                progressPercent.textContent = `${percent}%`;
            } else {
                progressPercent.textContent = '0%';
            }
            
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }
    
    // Function to convert pinyin with number tones to tone marks
    function convertPinyinToToneMarks(pinyin) {
        if (!pinyin) return '';
        
        // Define the vowel mappings for each tone
        const toneMap = {
            'a': ['ā', 'á', 'ǎ', 'à', 'a'],
            'e': ['ē', 'é', 'ě', 'è', 'e'],
            'i': ['ī', 'í', 'ǐ', 'ì', 'i'],
            'o': ['ō', 'ó', 'ǒ', 'ò', 'o'],
            'u': ['ū', 'ú', 'ǔ', 'ù', 'u'],
            'ü': ['ǖ', 'ǘ', 'ǚ', 'ǜ', 'ü'],
            'v': ['ǖ', 'ǘ', 'ǚ', 'ǜ', 'ü'] // v is sometimes used instead of ü
        };
        
        // Split the input by spaces to handle multiple words
        return pinyin.split(' ').map(word => {
            // Check if the word ends with a tone number (1-5)
            const toneNumMatch = word.match(/([a-zA-Zü]+)([1-5])$/);
            if (!toneNumMatch) return word; // No tone number found
            
            const syllable = toneNumMatch[1];
            const toneNum = parseInt(toneNumMatch[2], 10) - 1; // Convert to 0-based index
            
            // Find the vowel to modify with the tone mark
            // Priority: a, o, e, i, u, ü
            const vowelPriority = ['a', 'o', 'e', 'i', 'u', 'ü', 'v'];
            let vowelToChange = '';
            let vowelIndex = -1;
            
            // Check for special cases: "iu", "ui", "iu"
            if (syllable.includes('iu')) {
                vowelToChange = 'u';
                vowelIndex = syllable.lastIndexOf('u');
            } else if (syllable.includes('ui')) {
                vowelToChange = 'i';
                vowelIndex = syllable.lastIndexOf('i');
            } else {
                // Find the vowel with highest priority
                for (const vowel of vowelPriority) {
                    const idx = syllable.indexOf(vowel);
                    if (idx !== -1) {
                        vowelToChange = vowel;
                        vowelIndex = idx;
                        break;
                    }
                }
            }
            
            // If no vowel found, return the original word
            if (vowelIndex === -1) return word;
            
            // Replace the vowel with its tone mark version
            const newChar = toneMap[vowelToChange][toneNum];
            return syllable.substring(0, vowelIndex) + newChar + syllable.substring(vowelIndex + 1);
        }).join(' ');
    }
});
