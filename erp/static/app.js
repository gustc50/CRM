// Comportamentos comuns a todas as telas.

// ---- Confirmação antes de ações sem volta ----
// O texto vem em `data-confirmar`, como atributo HTML comum. Antes ele era
// escrito dentro de um `onsubmit="return confirm('...')"`: um nome com
// apóstrofo ("Conta d'água") quebrava o script, a pergunta não aparecia e a
// exclusão acontecia direto — mesmo para quem clicaria em Cancelar.
document.addEventListener('submit', function (evento) {
    var form = evento.target;
    var pergunta = form.getAttribute('data-confirmar');
    if (pergunta && !window.confirm(pergunta)) {
        evento.preventDefault();
    }
});

// ---- Campos de valor em reais ----
// Mesma regra do servidor (formatos.ler_valor): aceita "150,50", "1.234,56",
// "150.50". Ao sair do campo, mostra o valor como ficou entendido, para a
// pessoa conferir antes de salvar.
function lerValorBR(texto) {
    var t = (texto || '').replace(/R\$/g, '').replace(/\s/g, '');
    if (!t) { return null; }
    var virgula = t.indexOf(',') >= 0, ponto = t.indexOf('.') >= 0;
    if (virgula && ponto) {
        var dec = t.lastIndexOf(',') > t.lastIndexOf('.') ? ',' : '.';
        var mil = dec === ',' ? '.' : ',';
        var corte = t.lastIndexOf(dec);
        var grupos = t.slice(0, corte).split(mil);
        var centavos = t.slice(corte + 1);
        for (var i = 1; i < grupos.length; i++) {
            if (grupos[i].length !== 3) { return null; }
        }
        if (!grupos[0] || centavos.indexOf(mil) >= 0) { return null; }
        t = grupos.join('') + '.' + centavos;
    } else if (virgula) {
        if (t.split(',').length > 2) { return null; }
        t = t.replace(',', '.');
    } else if (ponto) {
        var partes = t.split('.');
        if (partes.length > 2 || partes[partes.length - 1].length === 3) {
            for (var j = 1; j < partes.length; j++) {
                if (partes[j].length !== 3) { return null; }
            }
            t = partes.join('');
        }
    }
    if (!/^\d+(\.\d{1,2})?$/.test(t)) { return null; }
    return parseFloat(t);
}

function escreverValorBR(numero) {
    return numero.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('input[data-valor-br]').forEach(function (campo) {
        function conferir(reescrever) {
            if (!campo.value.trim()) {
                campo.setCustomValidity('');
                return;
            }
            var numero = lerValorBR(campo.value);
            if (numero === null) {
                campo.setCustomValidity('Valor não reconhecido. Use vírgula para os centavos, como 1.234,56.');
            } else if (numero <= 0 && campo.hasAttribute('data-positivo')) {
                campo.setCustomValidity('O valor deve ser maior que zero.');
            } else {
                campo.setCustomValidity('');
                if (reescrever) { campo.value = escreverValorBR(numero); }
            }
        }
        campo.addEventListener('input', function () { conferir(false); });
        campo.addEventListener('blur', function () { conferir(true); });
        conferir(false);
    });

    // ---- Menu no celular ----
    // A lista inteira de abas ocupava meia tela antes do conteúdo em todas as
    // páginas. No celular ela fica recolhida atrás do botão "Menu".
    var botaoMenu = document.getElementById('botao-menu');
    var lateral = document.querySelector('.lateral');
    if (botaoMenu && lateral) {
        lateral.classList.add('menu-recolhivel');
        botaoMenu.addEventListener('click', function () {
            var aberto = lateral.classList.toggle('menu-aberto');
            botaoMenu.setAttribute('aria-expanded', aberto ? 'true' : 'false');
        });
    }

    // ---- Blocos que começam fechados no celular ----
    // Ex.: o formulário de novo lançamento e os filtros. No computador ficam
    // abertos; no celular, a lista aparece primeiro. Se o bloco voltou com um
    // erro para corrigir, ele continua aberto.
    if (window.matchMedia('(max-width: 860px)').matches) {
        document.querySelectorAll('details[data-fechar-no-celular]').forEach(function (bloco) {
            if (!bloco.hasAttribute('data-manter-aberto')) {
                bloco.open = false;
            }
        });
    }
});
