const service = {
    fetchData: async (id: string) => {
        return await fetch(`/api/data/${id}`);
    },
    'process-data': function(data: any) {
        console.log(data);
    },
    [Symbol.for('cleanup')]: () => {
        console.log('cleaning up');
    }
};

export default service;
