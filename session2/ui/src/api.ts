export type Status =
  | 'uploading'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';
export interface Segment {
  start: number;
  end: number;
  text: string;
}
export interface Job {
  id: string;
  name: string;
  extension: string;
  created_at: string;
  status: Status;
  stage: string;
  progress: number | null;
  duration: number | null;
  size: number;
  error: string | null;
  segments?: Segment[];
}
export interface Health {
  model_ready: boolean;
  model: string;
  max_bytes: number;
  max_seconds: number;
}

export const isActive = (job?: Job | null) =>
  !!job && ['uploading', 'processing'].includes(job.status);
export const time = (seconds: number) => {
  const value = Math.floor(Math.max(0, seconds));
  return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60]
    .map((n) => String(n).padStart(2, '0'))
    .join(':');
};
export const fileSize = (bytes: number) =>
  bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(1)} GiB`
    : `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
export async function api<T>(path: string, method = 'GET'): Promise<T> {
  const response = await fetch(`/api${path}`, { method, headers: { 'X-Local-Request': '1' } });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: 'The server could not complete this request.' }));
    throw new Error(data.detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export function upload(
  file: File,
  onProgress: (percent: number) => void,
  onXhr: (xhr: XMLHttpRequest) => void,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    onXhr(xhr);
    xhr.open('POST', '/api/jobs');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    xhr.setRequestHeader('X-Local-Request', '1');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error('The server returned an unreadable response.'));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || 'The upload failed. Try again.'));
    };
    xhr.onerror = () =>
      reject(new Error('Connection lost. Reconnect to the local server and try again.'));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    xhr.send(file);
  });
}
