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
}

document.addEventListener('DOMContentLoaded', function () {
    var tipoSelect = document.getElementById('tipo-select');
    if (tipoSelect) {
        tipoSelect.addEventListener('change', atualizarCampoEntidade);
        atualizarCampoEntidade();
    }
});
