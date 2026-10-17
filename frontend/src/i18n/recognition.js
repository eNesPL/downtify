// Shared English fallback until recognition strings are translated.
export default {
  menu: 'Recognize AMV song',
  title: 'Find the song behind an AMV',
  body: 'Identify the musician, keep your AMV audio, and discover similar songs.',
  link: 'YouTube / YouTube Music video link',
  useLink: 'Use a YouTube link instead',
  start: 'Find original song',
  audioConsent:
    'Use external audio recognition. Short samples may be sent to AudD; local fingerprints to AcoustID. Provider quotas or charges may apply.',
  noProvider:
    'Audio recognition is not configured on this server. Metadata search and manual correction still work.',
  source: 'Original source',
  uploader: 'Video uploader',
  openSource: 'Open original AMV',
  reviewHint:
    'Review the suggestions or enter the artist and song manually. Text search is a suggestion, not an audio identification. Saving preserves the audio, file tags and playlists.',
  accept: 'Save song identity',
  reject: 'Reject / remove identity',
  recent: 'Recent recognitions',
  scanTitle: 'Recognize existing library tracks',
  scanHint:
    'Analyze the next ten unprocessed files. Finished jobs are saved, so another batch continues where you stopped. Failed and interrupted jobs can be retried. Every identity still needs your approval.',
  scan: 'Scan next library batch',
  scanResult: 'Queued: {count}. Still unprocessed: {remaining}.',
  tagsHint:
    'Optional: write the approved title and artist into this shared library file. This affects all users and library discovery. The full original is backed up; audio and artwork are preserved.',
  applyTags: 'Apply title and artist to file tags',
  undoTags: 'Undo file tag correction',
  failed: 'Recognition failed',
  similarHint:
    'Know this song from an AMV? Identify the original musician to improve this mix.',
  usingOriginal: 'Recommendations use the approved song: {artist} — {title}',
  methods: {
    'youtube-metadata': 'YouTube music metadata',
    'text-search': 'Text search — verify',
    audd: 'AudD audio match',
    acoustid: 'AcoustID fingerprint',
    manual: 'Entered manually',
  },
  status: {
    queued: 'Queued',
    running: 'Recognizing…',
    review: 'Review song candidates',
    no_candidates: 'No match — you can enter the song manually',
    accepted: 'Song identity saved',
    rejected: 'Identity rejected',
    superseded: 'Replaced by a newer identity',
    interrupted: 'Interrupted — retry to continue',
    error: 'Recognition failed — retry or enter the song manually',
  },
}
