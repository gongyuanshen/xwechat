export const useMattersApi = () => {
  const base = `${useApiBase()}/matters`
  const request = (path, account, options = {}) => $fetch(`${base}${path}`, { ...options, query: { account } })
  return {
    list: account => request('', account),
    source: (account, source) => request('/source', account, { method: 'POST', body: source }),
    create: (account, body) => request('', account, { method: 'POST', body }),
    update: (account, id, body) => request(`/${encodeURIComponent(id)}`, account, { method: 'PATCH', body }),
    reschedule: (account, id, body) => request(`/${encodeURIComponent(id)}/reschedule`, account, { method: 'POST', body }),
  }
}
