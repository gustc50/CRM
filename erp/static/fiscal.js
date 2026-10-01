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

// Configurações de e-mail: servidor/porta/segurança só aparecem quando o
// provedor escolhido é "Outro servidor"; nos demais vêm prontos do preset.
document.addEventListener('DOMContentLoaded', function () {
    var provedor = document.getElementById('smtp-provedor');
    var manual = document.getElementById('smtp-manual');
    var aviso = document.getElementById('smtp-aviso');
    if (!provedor) {
        return;
    }

    function atualizar() {
        var opcao = provedor.options[provedor.selectedIndex];
        if (manual) {
            manual.classList.toggle('campo-oculto', provedor.value !== 'outro');
        }
        if (aviso && opcao) {
            aviso.textContent = opcao.getAttribute('data-aviso') || '';
        }
    }

    provedor.addEventListener('change', atualizar);
    atualizar();
});

// ---- Abas horizontais (A Receber / A Pagar nas telas de notas) ----
//
// Os dois painéis vêm visíveis no HTML: se esta função não rodar, a tela
// continua mostrando tudo empilhado, como era antes. Esconder é trabalho do
// JS, nunca do servidor.
(function () {
    document.querySelectorAll('.abas-horizontais').forEach(function (barra) {
        var chave = 'aba:' + (barra.dataset.abas || 'padrao');
        var escopo = barra.parentElement;
        var abas = barra.querySelectorAll('.aba');
        var paineis = escopo.querySelectorAll('.painel-aba');
        if (!abas.length || !paineis.length) { return; }

        function mostrar(alvo, lembrar) {
            abas.forEach(function (aba) {
                aba.setAttribute('aria-selected', aba.dataset.painel === alvo);
            });
            paineis.forEach(function (painel) {
                painel.hidden = painel.dataset.painel !== alvo;
            });
            if (lembrar) {
                // Conveniência de quem está olhando; se o navegador recusar
                // (janela anônima), a tela funciona igual.
                try { localStorage.setItem(chave, alvo); } catch (e) { /* ignora */ }
            }
        }

        abas.forEach(function (aba) {
            aba.addEventListener('click', function () { mostrar(aba.dataset.painel, true); });
        });

        var salva = null;
        try { salva = localStorage.getItem(chave); } catch (e) { /* ignora */ }
        mostrar(salva === 'pagar' ? 'pagar' : 'receber', false);
    });
})();
