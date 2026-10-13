import { readAttachmentDownload } from '~/utils/attachmentDownload';

export const useLibraryApi = () => {
  const base = `${useApiBase()}/library`;
  const request = (path, account, options = {}) =>
    $fetch(`${base}/${path}`, {
      ...options,
      query: { account, ...options.query },
    });
  const api = {};
  for (const [name, resource] of [
    ["Folder", "folders"],
    ["Item", "items"],
    ["Search", "searches"],
  ]) {
    api[name === "Search" ? "listSearches" : `list${name}s`] = (
      account,
      folderId,
    ) =>
      request(
        resource,
        account,
        resource === "items" && folderId
          ? { query: { folder_id: folderId } }
          : {},
      );
    api[`create${name}`] = (account, body) =>
      request(resource, account, { method: "POST", body });
    api[`update${name}`] = (account, id, body) =>
      request(`${resource}/${encodeURIComponent(id)}`, account, {
        method: "PATCH",
        body,
      });
    api[`delete${name}`] = (account, id) =>
      request(`${resource}/${encodeURIComponent(id)}`, account, {
        method: "DELETE",
      });
  }
  api.getItem = (account, id) =>
    request(`items/${encodeURIComponent(id)}`, account);
  api.getDossier = (account, folderId) =>
    request(`folders/${encodeURIComponent(folderId)}/dossier`, account);
  api.saveVersion = (account, folderId, body) =>
    request(`folders/${encodeURIComponent(folderId)}/versions`, account, { method: 'POST', body });
  api.getVersion = (account, folderId, versionId) =>
    request(`folders/${encodeURIComponent(folderId)}/versions/${encodeURIComponent(versionId)}`, account);
  api.downloadVersionAttachment = async (account, folderId, versionId, itemId) =>
    readAttachmentDownload(await fetch(`${base}/folders/${encodeURIComponent(folderId)}/versions/${encodeURIComponent(versionId)}/attachments/${encodeURIComponent(itemId)}?${new URLSearchParams({ account })}`));
  api.exportItem = (account, id, format) =>
    request(`items/${encodeURIComponent(id)}/export`, account, {
      query: { format },
      responseType: "blob",
    });
  api.attachmentUrl = (account, id) =>
    `${base}/items/${encodeURIComponent(id)}/attachment?${new URLSearchParams({ account, download: "false" })}`;
  api.downloadAttachment = async (account, id) =>
    readAttachmentDownload(await fetch(`${base}/items/${encodeURIComponent(id)}/attachment?${new URLSearchParams({ account, download: "true" })}`));
  return api;
};
