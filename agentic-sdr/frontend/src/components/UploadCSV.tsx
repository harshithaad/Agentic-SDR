import { useCallback, useRef, useState } from 'react'
import axios from 'axios'

type UploadState = 'idle' | 'dragging' | 'uploading' | 'done' | 'error'

export default function UploadCSV() {
  const [uploadState, setUploadState] = useState<UploadState>('idle')
  const [message, setMessage] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = async (file: File) => {
    if (!file.name.endsWith('.csv')) {
      setMessage('Please upload a CSV file.')
      setUploadState('error')
      return
    }

    setUploadState('uploading')
    setMessage('Uploading...')

    const form = new FormData()
    form.append('file', file)

    try {
      const res = await axios.post('/api/leads/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const count = res.data.created
      setMessage(`Successfully uploaded ${count} lead${count !== 1 ? 's' : ''}. Processing has started.`)
      setUploadState('done')
    } catch (e: any) {
      const detail = e?.response?.data?.detail || 'Upload failed. Please try again.'
      setMessage(detail)
      setUploadState('error')
    }
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setUploadState('idle')
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [])

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setUploadState('dragging')
  }

  const onDragLeave = () => {
    setUploadState('idle')
  }

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    e.target.value = ''
  }

  const borderColor =
    uploadState === 'dragging'
      ? 'border-indigo-400 bg-indigo-950/30'
      : uploadState === 'done'
      ? 'border-green-600 bg-green-950/20'
      : uploadState === 'error'
      ? 'border-red-600 bg-red-950/20'
      : 'border-gray-700 hover:border-gray-600'

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      <h2 className="text-base font-semibold text-white mb-4">Upload Leads</h2>

      <div
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all ${borderColor}`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={onInputChange}
        />

        {uploadState === 'uploading' ? (
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-gray-400 text-sm">Uploading leads...</p>
          </div>
        ) : uploadState === 'done' ? (
          <div className="flex flex-col items-center gap-2">
            <div className="text-3xl">✓</div>
            <p className="text-green-400 text-sm font-medium">{message}</p>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setUploadState('idle')
                setMessage('')
              }}
              className="text-xs text-gray-500 hover:text-gray-400 mt-1 transition-colors"
            >
              Upload another file
            </button>
          </div>
        ) : uploadState === 'error' ? (
          <div className="flex flex-col items-center gap-2">
            <div className="text-3xl">✕</div>
            <p className="text-red-400 text-sm">{message}</p>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setUploadState('idle')
                setMessage('')
              }}
              className="text-xs text-gray-500 hover:text-gray-400 mt-1 transition-colors"
            >
              Try again
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-gray-800 flex items-center justify-center text-2xl">
              📄
            </div>
            <div>
              <p className="text-white font-medium text-sm">
                {uploadState === 'dragging' ? 'Drop to upload' : 'Drop CSV here or click to browse'}
              </p>
              <p className="text-gray-500 text-xs mt-1">
                Expected columns: <code className="bg-gray-800 px-1 rounded">company_name</code>,{' '}
                <code className="bg-gray-800 px-1 rounded">website</code> (optional)
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
