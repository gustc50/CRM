// Acompanha a sincronização de notas fiscais em segundo plano.
// Quando a página carrega com uma sincronização em execução, consulta o
// status a cada 2 segundos e recarrega a página ao terminar.
document.addEventListener('DOMContentLoaded', function () {
    var barra = document.getElementById('sync-fiscal');
    if (!barra || barra.dataset.estado !== 'executando') {
        return;
    }

    var url = barra.dataset.url;
    var mensagem = document.getElementById('sync-mensagem');

    function verificar() {
        fetch(url)
            .then(function (resp) { return resp.json(); })
            .then(function (status) {
                if (status.estado === 'executando') {
                    if (mensagem && status.mensagem) {
                        mensagem.textContent = status.mensagem
                            + (status.novos ? ' (' + status.novos + ' nova(s) até agora)' : '');
                    }
                    setTimeout(verificar, 2000);
                } else {
                    window.location.reload();
                }
            })
            .catch(function () {
                setTimeout(verificar, 4000);
            });
    }

    setTimeout(verificar, 2000);
});
