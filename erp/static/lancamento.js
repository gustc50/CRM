function atualizarCampoEntidade() {
    var tipoSelect = document.getElementById('tipo-select');
    var fornecedorSelect = document.getElementById('fornecedor-select');
    var clienteSelect = document.getElementById('cliente-select');
    if (!tipoSelect || !fornecedorSelect || !clienteSelect) {
        return;
    }

    var ehPagar = tipoSelect.value === 'Pagar';

    fornecedorSelect.classList.toggle('campo-oculto', !ehPagar);
    fornecedorSelect.disabled = !ehPagar;
    fornecedorSelect.required = ehPagar;

    clienteSelect.classList.toggle('campo-oculto', ehPagar);
    clienteSelect.disabled = ehPagar;
    clienteSelect.required = !ehPagar;

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
}

document.addEventListener('DOMContentLoaded', function () {
    var tipoSelect = document.getElementById('tipo-select');
    if (tipoSelect) {
        tipoSelect.addEventListener('change', atualizarCampoEntidade);
        atualizarCampoEntidade();
    }
});
