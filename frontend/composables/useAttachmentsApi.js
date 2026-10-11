import { readAttachmentDownload } from '~/utils/attachmentDownload';

export const useAttachmentsApi = () => {
  const base = `${useApiBase()}/attachments`;
  return {
    list: (account, query, signal) => $fetch(base, { query: { ...query, account }, signal, retry: 0 }),
    filters: (account, signal) => $fetch(`${base}/filters`, { query: { account }, signal, retry: 0 }),
    extract: (account, source, signal) => $fetch(`${base}/extract`, {
      method: 'POST', query: { account }, body: source, signal, retry: 0,
    }),
    text: (account, source, signal) => $fetch(`${base}/text`, { query: { account, ...source }, signal, retry: 0 }),
    contentUrl: (account, item) => `${base}/content?${new URLSearchParams({ account, username: item.username, anchor: item.id, download: 'false' })}`,
    download: async (account, item, signal) => readAttachmentDownload(await fetch(`${base}/content?${new URLSearchParams({ account, username: item.username, anchor: item.id, download: 'true' })}`, { signal })),
  };
};
