export const useUnifiedSearchApi = () => {
  const base = `${useApiBase()}/search`;
  return {
    search: (account, query, signal) => $fetch(base, { query: { account, ...query }, signal, retry: 0 }),
  };
};
