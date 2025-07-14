/**
 * Chunked File Uploader
 * 
 * Handles large file uploads by splitting them into smaller chunks
 * to avoid Cloudflare's 100MB limit. Works without external dependencies.
 */
class ChunkedUploader {
    constructor(options = {}) {
        this.chunkSize = options.chunkSize || 10 * 1024 * 1024; // 10MB default
        this.retries = options.retries || 3;
        this.concurrentChunks = options.concurrentChunks || 3;
        this.uploadEndpoint = options.uploadEndpoint || '/api/upload/chunk';
        this.completeEndpoint = options.completeEndpoint || '/api/upload/complete';
        this.statusEndpoint = options.statusEndpoint || '/api/upload/status';
        
        // Status tracking
        this.activeChunks = 0;
        this.pendingChunks = [];
        this.completedChunks = [];
        this.failedChunks = [];
        this.aborted = false;
        this.uploadId = null;
        
        // Callbacks
        this.onProgress = options.onProgress || function() {};
        this.onComplete = options.onComplete || function() {};
        this.onError = options.onError || function() {};
        this.onChunkComplete = options.onChunkComplete || function() {};
        this.onChunkError = options.onChunkError || function() {};
        
        // Bind methods
        this.processNextChunk = this.processNextChunk.bind(this);
        this.uploadChunk = this.uploadChunk.bind(this);
        this.completeUpload = this.completeUpload.bind(this);
        this.generateUploadId = this.generateUploadId.bind(this);
        this.handleChunkError = this.handleChunkError.bind(this);
    }
    
    /**
     * Start uploading a file in chunks
     * @param {File} file - The file to upload
     * @returns {Promise} - Resolves when upload completes
     */
    upload(file) {
        if (!file) {
            this.onError(new Error('No file provided'));
            return Promise.reject(new Error('No file provided'));
        }
        
        return new Promise((resolve, reject) => {
            this.file = file;
            this.totalSize = file.size;
            this.totalChunks = Math.ceil(file.size / this.chunkSize);
            this.uploadId = this.uploadId || this.generateUploadId();
            this.pendingChunks = [];
            this.completedChunks = [];
            this.failedChunks = [];
            this.aborted = false;
            
            // Setup all chunks
            for (let i = 0; i < this.totalChunks; i++) {
                this.pendingChunks.push(i);
            }
            
            // Start upload process
            for (let i = 0; i < Math.min(this.concurrentChunks, this.totalChunks); i++) {
                this.processNextChunk();
            }
            
            // Store resolve/reject for later use
            this.resolveUpload = resolve;
            this.rejectUpload = reject;
        });
    }
    
    /**
     * Process the next chunk in queue
     */
    processNextChunk() {
        if (this.aborted) return;
        
        // If no more pending chunks, check if we're done
        if (this.pendingChunks.length === 0) {
            if (this.activeChunks === 0 && this.failedChunks.length === 0) {
                this.completeUpload();
            }
            return;
        }
        
        // Get next chunk from queue
        const chunkIndex = this.pendingChunks.shift();
        this.activeChunks++;
        
        // Calculate chunk boundaries
        const start = chunkIndex * this.chunkSize;
        const end = Math.min(start + this.chunkSize, this.totalSize);
        
        // Get the chunk data as Blob
        const chunk = this.file.slice(start, end);
        
        // Upload this chunk
        this.uploadChunk(chunk, chunkIndex, 0);
    }
    
    /**
     * Upload a single chunk
     * @param {Blob} chunk - The chunk data to upload
     * @param {Number} chunkIndex - Index of this chunk
     * @param {Number} retryCount - Number of times this chunk has been retried
     */
    uploadChunk(chunk, chunkIndex, retryCount) {
        if (this.aborted) return;
        
        const formData = new FormData();
        formData.append('file', chunk, this.file.name);
        formData.append('filename', this.file.name);
        formData.append('chunkIndex', chunkIndex);
        formData.append('totalChunks', this.totalChunks);
        formData.append('uploadId', this.uploadId);
        
        // Проверяем соединение перед загрузкой
        if (!navigator.onLine) {
            const error = new Error('Нет подключения к интернету');
            this.handleChunkError(chunk, chunkIndex, retryCount, error);
            return;
        }
        
        // Устанавливаем таймаут для fetch
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 сек таймаут
        
        fetch(this.uploadEndpoint, {
            method: 'POST',
            body: formData,
            credentials: 'same-origin',
            signal: controller.signal
        })
        .then(response => {
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                // Более детальная информация об HTTP-ошибках
                const errorMessages = {
                    400: 'Неверный запрос',
                    401: 'Требуется авторизация',
                    403: 'Доступ запрещен',
                    404: 'API не найден',
                    413: 'Файл слишком большой',
                    500: 'Ошибка сервера',
                    503: 'Сервис недоступен'
                };
                
                const msg = errorMessages[response.status] || `HTTP ошибка ${response.status}`;
                throw new Error(msg);
            }
            
            return response.json();
        })
        .then(result => {
            if (!result.success) {
                throw new Error(result.error || 'Unknown error');
            }
            
            // Mark chunk as complete
            this.completedChunks.push(chunkIndex);
            this.activeChunks--;
            
            // Report progress
            const progress = {
                totalChunks: this.totalChunks,
                completedChunks: this.completedChunks.length,
                percent: Math.round((this.completedChunks.length / this.totalChunks) * 100),
                uploadId: this.uploadId
            };
            
            this.onChunkComplete(chunkIndex, progress);
            this.onProgress(progress);
            
            // Process next chunk
            this.processNextChunk();
        })
        .catch(error => {
            clearTimeout(timeoutId);
            this.handleChunkError(chunk, chunkIndex, retryCount, error);
        });
    }
    
    /**
     * Обработка ошибок при загрузке чанка
     * @param {Blob} chunk - The chunk data
     * @param {Number} chunkIndex - Index of the chunk
     * @param {Number} retryCount - Number of retry attempts so far
     * @param {Error} error - The error that occurred
     */
    handleChunkError(chunk, chunkIndex, retryCount, error) {
        this.activeChunks--;
        
        // Определяем тип ошибки
        let errorMessage = error.message || 'Неизвестная ошибка';
        const isNetworkError = (
            error.name === 'TypeError' || 
            errorMessage.includes('network') || 
            errorMessage.includes('connection') ||
            error.name === 'AbortError' ||
            !navigator.onLine
        );
        
        console.warn(`Chunk ${chunkIndex} failed: ${errorMessage} (network issue: ${isNetworkError})`);
        
        // Стратегия повторных попыток в зависимости от типа ошибки
        const shouldRetry = retryCount < this.retries && (
            isNetworkError || // всегда повторяем при сетевых ошибках
            error.name === 'AbortError' || // таймаут
            (error.message && error.message.includes('500')) // ошибка сервера
        );
        
        if (shouldRetry) {
            // Экспоненциальная задержка при повторе
            const delay = Math.min(1000 * Math.pow(2, retryCount), 10000); // макс 10 сек
            console.warn(`Retrying chunk ${chunkIndex} in ${delay}ms (attempt ${retryCount + 1}/${this.retries})`);
            
            setTimeout(() => {
                // Проверка сети перед повторной попыткой
                if (!navigator.onLine) {
                    this.handleChunkError(
                        chunk, chunkIndex, this.retries, 
                        new Error('Нет подключения к интернету')
                    );
                    return;
                }
                this.uploadChunk(chunk, chunkIndex, retryCount + 1);
            }, delay);
        } else {
            // Mark as failed
            this.failedChunks.push(chunkIndex);
            this.onChunkError(chunkIndex, error);
            
            // If all chunks are done (completed or failed), finish the upload
            if (this.pendingChunks.length === 0 && this.activeChunks === 0) {
                if (this.failedChunks.length > 0) {
                    const errorObj = new Error(`Ошибка загрузки: не удалось загрузить ${this.failedChunks.length} из ${this.totalChunks} фрагментов`);
                    errorObj.failedChunks = this.failedChunks;
                    this.onError(errorObj);
                    this.rejectUpload(errorObj);
                } else {
                    this.completeUpload();
                }
            } else {
                // Process next chunk
                this.processNextChunk();
            }
        }
    }
    
    /**
     * Tell the server to combine all uploaded chunks
     */
    completeUpload() {
        if (this.completedChunks.length !== this.totalChunks) {
            const error = new Error(`Cannot complete upload: ${this.completedChunks.length}/${this.totalChunks} chunks completed`);
            this.onError(error);
            this.rejectUpload(error);
            return;
        }
        
        // Tell server to combine chunks
        fetch(this.completeEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                uploadId: this.uploadId
            }),
            credentials: 'same-origin'
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server returned ${response.status}`);
            }
            return response.json();
        })
        .then(result => {
            if (result.error) {
                throw new Error(result.error);
            }
            
            // Success - file is ready on server
            this.onComplete(result);
            this.resolveUpload(result);
        })
        .catch(error => {
            this.onError(error);
            this.rejectUpload(error);
        });
    }
    
    /**
     * Abort the current upload
     */
    abort() {
        this.aborted = true;
        this.onError(new Error('Upload aborted'));
        if (this.rejectUpload) {
            this.rejectUpload(new Error('Upload aborted'));
        }
    }
    
    /**
     * Generate a random ID for this upload
     * @returns {String} - Random ID
     */
    generateUploadId() {
        return 'upload_' + Math.random().toString(36).substring(2, 15) + 
               Math.random().toString(36).substring(2, 15);
    }
    
    /**
     * Check the status of an existing upload
     * @param {String} uploadId - The upload ID to check
     * @returns {Promise} - Resolves with status
     */
    static checkStatus(uploadId, endpoint = '/api/upload/status') {
        return fetch(`${endpoint}?uploadId=${encodeURIComponent(uploadId)}`, {
            credentials: 'same-origin'
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server returned ${response.status}`);
            }
            return response.json();
        });
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ChunkedUploader;
}
