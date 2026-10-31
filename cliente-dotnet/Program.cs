using System;
using Grpc.Net.Client;
using Reservas.V1;

using var canal = GrpcChannel.ForAddress("http://localhost:50051");
var cliente = new ReservaService.ReservaServiceClient(canal);
var respuesta = await cliente.ListarStockAsync(new ListarStockRequest());

foreach (var item in respuesta.Items)
    Console.WriteLine($"{item.TipoSangre}: {item.UnidadesDisponibles}");
