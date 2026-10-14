function atualizarCampoEntidade() {
    var tipoSelect = document.getElementById('tipo-select');
    var fornecedorSelect = document.getElementById('fornecedor-select');
    var clienteSelect = document.getElementById('cliente-select');
    if (!tipoSelect || !fornecedorSelect || !clienteSelect) {
        return;
    }

    var ehPagar = tipoSelect.value === 'Pagar';

    // Fornecedor e cliente são opcionais: dá para lançar uma tarifa sem
    // cadastrar o banco antes.
    fornecedorSelect.classList.toggle('campo-oculto', !ehPagar);
    fornecedorSelect.disabled = !ehPagar;

    clienteSelect.classList.toggle('campo-oculto', ehPagar);
    clienteSelect.disabled = ehPagar;

    // Quando o <select> está dentro de um <label> próprio (telas de edição),
    // esconde o rótulo inteiro para não deixar o texto "órfão" na tela.
    var fornecedorCampo = document.getElementById('fornecedor-campo');
    var clienteCampo = document.getElementById('cliente-campo');
    if (fornecedorCampo) {
        fornecedorCampo.classList.toggle('campo-oculto', !ehPagar);
    }
    if (clienteCampo) {
        clienteCampo.classList.toggle('campo-oculto', ehPagar);
    }

    // A categoria segue a mesma regra: só a lista do tipo certo fica visível
    // e habilitada (as duas usam name="categoria_id" — só a ativa é enviada).
    var categoriaPagarSelect = document.getElementById('categoria-pagar-select');
    var categoriaReceberSelect = document.getElementById('categoria-receber-select');
    var categoriaPagarCampo = document.getElementById('categoria-pagar-campo');
    var categoriaReceberCampo = document.getElementById('categoria-receber-campo');

    if (categoriaPagarSelect) {
        categoriaPagarSelect.classList.toggle('campo-oculto', !ehPagar);
        categoriaPagarSelect.disabled = !ehPagar;
    }
    if (categoriaReceberSelect) {
        categoriaReceberSelect.classList.toggle('campo-oculto', ehPagar);
        categoriaReceberSelect.disabled = ehPagar;
    }
    if (categoriaPagarCampo) {
        categoriaPagarCampo.classList.toggle('campo-oculto', !ehPagar);
    }
    if (categoriaReceberCampo) {
        categoriaReceberCampo.classList.toggle('campo-oculto', ehPagar);
    }

    atualizarCadastroNovo();
}

// "+ Cadastrar novo…" na lista abre o campo do nome, que passa a ser
// obrigatório; qualquer outra escolha esconde o campo de novo.
var NOVO_CADASTRO = '__novo__';

function atualizarCadastroNovo() {
    var tipoSelect = document.getElementById('tipo-select');
    var ehPagar = tipoSelect && tipoSelect.value === 'Pagar';
    [['fornecedor', ehPagar], ['cliente', !ehPagar]].forEach(function (par) {
        var select = document.getElementById(par[0] + '-select');
        var campo = document.getElementById(par[0] + '-novo-campo');
        if (!select || !campo) {
            return;
        }
        var input = campo.querySelector('input');
        var mostrar = par[1] && select.value === NOVO_CADASTRO;
        campo.classList.toggle('campo-oculto', !mostrar);
        input.disabled = !mostrar;
        input.required = mostrar;
    });
}

// Quando o usuário escolhe um fornecedor/cliente que já tem uma categoria
// e/ou conta bancária padrão cadastradas, pré-seleciona esses valores
// automaticamente (o usuário ainda pode trocar à vontade antes de salvar).
function aplicarValorPadrao(selectOrigem, selectDestinoId, atributo) {
    var selectDestino = document.getElementById(selectDestinoId);
    if (!selectOrigem || !selectDestino) {
        return;
    }
    var opcao = selectOrigem.options[selectOrigem.selectedIndex];
    var valor = opcao ? opcao.getAttribute(atributo) : '';
    if (valor) {
        selectDestino.value = valor;
    }
}

document.addEventListener('DOMContentLoaded', function () {
    var tipoSelect = document.getElementById('tipo-select');
    if (tipoSelect) {
        tipoSelect.addEventListener('change', atualizarCampoEntidade);
        atualizarCampoEntidade();
    }

    var fornecedorSelect = document.getElementById('fornecedor-select');
    if (fornecedorSelect) {
        fornecedorSelect.addEventListener('change', function () {
            atualizarCadastroNovo();
            if (fornecedorSelect.value === NOVO_CADASTRO) {
                document.querySelector('#fornecedor-novo-campo input').focus();
            }
            aplicarValorPadrao(fornecedorSelect, 'categoria-pagar-select', 'data-categoria');
            aplicarValorPadrao(fornecedorSelect, 'conta-bancaria-select', 'data-conta');
        });
    }
    var clienteSelect = document.getElementById('cliente-select');
    if (clienteSelect) {
        clienteSelect.addEventListener('change', function () {
            atualizarCadastroNovo();
            if (clienteSelect.value === NOVO_CADASTRO) {
                document.querySelector('#cliente-novo-campo input').focus();
            }
            aplicarValorPadrao(clienteSelect, 'categoria-receber-select', 'data-categoria');
            aplicarValorPadrao(clienteSelect, 'conta-bancaria-select', 'data-conta');
        });
    }
});
